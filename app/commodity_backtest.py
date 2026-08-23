from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

import httpx

from .commodities import analyze_commodity_candles, resolve_nearest_mcx_future

IST = ZoneInfo("Asia/Kolkata")


def _f(value, default=0.0):
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _ts(value):
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        raw = float(value)
        if raw > 1_000_000_000_000:
            raw /= 1000
        return datetime.fromtimestamp(raw, IST)
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _direction_strength(frame, action):
    signal = str(frame.get("signal") or "NO TRADE").upper()
    alpha = _f(frame.get("alpha_score"), 50)
    if signal != action:
        return 50.0
    return alpha if action == "BUY" else 100.0 - alpha


async def _fetch_range(provider, contract, interval_minutes, start, end):
    params = {
        "exchange": contract["exchange"],
        "segment": contract["segment"],
        "trading_symbol": contract["trading_symbol"],
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
        "interval_in_minutes": str(interval_minutes),
    }
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.get(
            f"{provider.BASE_URL}/v1/historical/candle/range",
            headers=await provider._headers(),
            params=params,
        )
    response.raise_for_status()
    data = response.json()
    payload = data.get("payload", data)
    return payload.get("candles", []) if isinstance(payload, dict) else []


async def _fetch_chunked(provider, contract, interval_minutes, start, end):
    rows = []
    cursor = start
    chunk_days = 7 if interval_minutes <= 15 else 30
    while cursor < end:
        chunk_end = min(end, cursor + timedelta(days=chunk_days))
        chunk = await _fetch_range(provider, contract, interval_minutes, cursor, chunk_end)
        rows.extend(chunk)
        cursor = chunk_end + timedelta(seconds=1)
    dedup = {}
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 5:
            dedup[str(row[0])] = list(row)
    return sorted(dedup.values(), key=lambda row: _ts(row[0]))


def _slice_until(rows, when):
    eligible = [row for row in rows if _ts(row[0]) <= when]
    return eligible[-260:]


def _plan_at(symbol, frames, min_rr, strength_threshold):
    directions = [str(frames[tf].get("signal") or "NO TRADE") for tf in ("5m", "15m", "1h")]
    buy_count = directions.count("BUY")
    sell_count = directions.count("SELL")
    action = "BUY" if buy_count >= 2 else "SELL" if sell_count >= 2 else "NO TRADE"
    if action == "NO TRADE":
        return None
    strength = round(mean([_direction_strength(frames[tf], action) for tf in ("5m", "15m", "1h")]), 1)
    five_rsi = _f(frames["5m"].get("rsi14"), 50)
    exhausted = (action == "BUY" and five_rsi >= 78) or (action == "SELL" and five_rsi <= 22)
    if strength < strength_threshold or exhausted:
        return None
    reference = next((frames[tf] for tf in ("15m", "5m", "1h") if frames[tf].get("signal") == action and frames[tf].get("status") == "SETUP"), None)
    if not reference:
        return None
    entry, stop, t1, t2 = map(_f, (reference.get("entry"), reference.get("stop_loss"), reference.get("target1"), reference.get("target2")))
    if min(entry, stop, t1, t2) <= 0:
        return None
    return {"symbol": symbol, "action": action, "strength": strength, "entry": entry, "stop": stop, "target1": t1, "target2": t2, "rsi5": five_rsi, "min_rr": min_rr}


def _resolve_trade(plan, future_rows, entry_time, slippage_bps, cost_bps):
    action = plan["action"]
    entry = plan["entry"]
    stop = plan["stop"]
    t1 = plan["target1"]
    t2 = plan["target2"]
    risk = (entry - stop) if action == "BUY" else (stop - entry)
    if risk <= 0:
        return {"outcome": "INVALID", "r_multiple": 0.0, "exit_time": None, "t2_touched": False}

    for row in future_rows:
        when = _ts(row[0])
        if when <= entry_time or len(row) < 4:
            continue
        high, low = _f(row[2]), _f(row[3])
        if action == "BUY":
            hit_stop, hit_t1, hit_t2 = low <= stop, high >= t1, high >= t2
        else:
            hit_stop, hit_t1, hit_t2 = high >= stop, low <= t1, low <= t2

        # With OHLC-only data the intrabar sequence is unknowable when stop and target
        # are both touched in the same candle, so do not fabricate an outcome.
        if hit_stop and hit_t1:
            return {"outcome": "AMBIGUOUS", "r_multiple": 0.0, "exit_time": when.isoformat(), "t2_touched": bool(hit_t2)}

        # Frozen baseline exits 100% at T1. Even if the same candle later reaches T2,
        # the booked result remains T1 because the position would already be closed.
        if hit_t1:
            gross_r = abs(t1 - entry) / risk
            return {
                "outcome": "T1_HIT",
                "r_multiple": round(gross_r - _cost_r(entry, risk, slippage_bps, cost_bps), 3),
                "exit_time": when.isoformat(),
                "t2_touched": bool(hit_t2),
            }
        if hit_stop:
            return {
                "outcome": "SL_HIT",
                "r_multiple": round(-1.0 - _cost_r(entry, risk, slippage_bps, cost_bps), 3),
                "exit_time": when.isoformat(),
                "t2_touched": False,
            }

    return {"outcome": "OPEN", "r_multiple": 0.0, "exit_time": None, "t2_touched": False}


def _cost_r(entry, risk, slippage_bps, cost_bps):
    round_trip_fraction = 2.0 * (max(0.0, slippage_bps) + max(0.0, cost_bps)) / 10000.0
    return (entry * round_trip_fraction) / risk if risk > 0 else 0.0


def _summary(trades):
    resolved = [t for t in trades if t["outcome"] in {"T1_HIT", "SL_HIT"}]
    wins = [t for t in resolved if t["outcome"] == "T1_HIT"]
    losses = [t for t in resolved if t["outcome"] == "SL_HIT"]
    rs = [t["r_multiple"] for t in resolved]
    gross_wins = sum(max(0.0, r) for r in rs)
    gross_losses = abs(sum(min(0.0, r) for r in rs))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    max_loss_streak = 0
    streak = 0
    for trade in resolved:
        equity += trade["r_multiple"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if trade["outcome"] == "SL_HIT":
            streak += 1
            max_loss_streak = max(max_loss_streak, streak)
        else:
            streak = 0
    return {
        "trades": len(trades),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round((len(wins) / len(resolved) * 100), 2) if resolved else 0.0,
        "t1_hits": sum(t["outcome"] == "T1_HIT" for t in trades),
        "t2_hits": 0,
        "t2_touched": sum(bool(t.get("t2_touched")) for t in trades),
        "sl_hits": sum(t["outcome"] == "SL_HIT" for t in trades),
        "ambiguous": sum(t["outcome"] == "AMBIGUOUS" for t in trades),
        "open": sum(t["outcome"] == "OPEN" for t in trades),
        "expectancy_r": round(mean(rs), 3) if rs else 0.0,
        "profit_factor": round(gross_wins / gross_losses, 3) if gross_losses > 0 else None,
        "net_r": round(sum(rs), 3),
        "max_drawdown_r": round(max_dd, 3),
        "max_consecutive_losses": max_loss_streak,
    }


async def run_commodity_backtest(provider, symbol, days=30, min_rr=1.5, strength_threshold=65.0, slippage_bps=2.0, cost_bps=2.0):
    symbol = str(symbol or "").strip().upper()
    days = max(7, min(int(days), 90))
    contract = await resolve_nearest_mcx_future(symbol)
    end = datetime.now(IST)
    start = end - timedelta(days=days)

    data = {
        "5m": await _fetch_chunked(provider, contract, 5, start, end),
        "15m": await _fetch_chunked(provider, contract, 15, start, end),
        "1h": await _fetch_chunked(provider, contract, 60, start, end),
    }
    if not data["5m"]:
        raise RuntimeError(f"No 5m history returned for {contract['trading_symbol']}")

    checkpoints = [_ts(row[0]) for row in data["15m"]]
    trades = []
    busy_until = None
    for when in checkpoints:
        if busy_until and when <= busy_until:
            continue
        frames = {}
        valid = True
        for tf in ("5m", "15m", "1h"):
            history = _slice_until(data[tf], when)
            analysis = analyze_commodity_candles(symbol, history, min_rr)
            if len(history) < 60:
                valid = False
                break
            frames[tf] = analysis
        if not valid:
            continue
        plan = _plan_at(symbol, frames, min_rr, strength_threshold)
        if not plan:
            continue
        outcome = _resolve_trade(plan, data["5m"], when, slippage_bps, cost_bps)
        trade = {
            **plan,
            **outcome,
            "entry_time": when.isoformat(),
            "timeframe_signals": {tf: frames[tf].get("signal") for tf in frames},
            "timeframe_alpha": {tf: _f(frames[tf].get("alpha_score"), 50) for tf in frames},
        }
        trades.append(trade)
        if outcome.get("exit_time"):
            busy_until = datetime.fromisoformat(outcome["exit_time"])
        else:
            busy_until = end

    coverage_start = min((_ts(row[0]) for row in data["5m"]), default=None)
    coverage_end = max((_ts(row[0]) for row in data["5m"]), default=None)
    return {
        "mode": "MCX_CONTRACT_WINDOW_BACKTEST",
        "symbol": symbol,
        "contract": contract,
        "requested_days": days,
        "coverage": {
            "start": coverage_start.isoformat() if coverage_start else None,
            "end": coverage_end.isoformat() if coverage_end else None,
            "candles": {tf: len(rows) for tf, rows in data.items()},
        },
        "rules": {
            "timeframes": ["5m", "15m", "1h"],
            "evaluation_interval": "15m completed bars",
            "min_risk_reward": min_rr,
            "directional_strength_min": strength_threshold,
            "rsi_exhaustion": {"buy_block_at_or_above": 78, "sell_block_at_or_below": 22},
            "slippage_bps_each_side": slippage_bps,
            "cost_bps_each_side": cost_bps,
            "overlapping_trades": False,
            "lookahead": False,
            "exit_model": "100% at T1",
        },
        "summary": _summary(trades),
        "by_action": {
            "BUY": _summary([t for t in trades if t["action"] == "BUY"]),
            "SELL": _summary([t for t in trades if t["action"] == "SELL"]),
        },
        "trades": trades[-200:],
        "limitations": [
            "This is a single-current-contract window, not a continuous 6-12 month rolled futures series.",
            "The frozen baseline exits 100% at T1. T2 touched only records that the exit candle also extended to T2; it is not booked as a T2 profit.",
            "Brokerage, taxes and slippage are approximated using configurable basis-point costs rather than broker contract-note charges.",
        ],
    }
