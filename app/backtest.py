from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.engine import analyze_candles, clean_candles

IST = ZoneInfo("Asia/Kolkata")
WEIGHTS = {"5m": 0.20, "15m": 0.35, "1h": 0.30}
INTERVALS = {"5m": "5minute", "15m": "15minute", "1h": "1hour"}


def _ts(value):
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=IST) if "+" not in str(value) else datetime.fromisoformat(str(value)).astimezone(IST)
    except Exception:
        return None


def _directional_mtf(tf):
    valid = [v for v in tf.values() if v.get("status") != "ERROR"]
    if not valid:
        return None
    weight_used = sum(WEIGHTS[t] for t in tf if tf[t].get("status") != "ERROR")
    score = sum(tf[t].get("alpha_score", 50) * WEIGHTS[t] for t in tf if tf[t].get("status") != "ERROR") / weight_used
    long_votes = sum(1 for v in valid if v.get("signal") in ("LONG", "STRONG_LONG", "WATCH_LONG") and v.get("alpha_score", 0) >= 58)
    short_votes = sum(1 for v in valid if v.get("signal") in ("SHORT", "STRONG_SHORT", "WATCH_SHORT") and v.get("alpha_score", 100) <= 42)
    setup_long = sum(1 for v in valid if v.get("status") == "SETUP" and v.get("direction") == "LONG")
    setup_short = sum(1 for v in valid if v.get("status") == "SETUP" and v.get("direction") == "SHORT")
    higher_bull = tf.get("1h", {}).get("alpha_score", 50) >= 55
    higher_bear = tf.get("1h", {}).get("alpha_score", 50) <= 45
    n = len(valid)
    if score >= 68 and long_votes >= max(2, n - 1) and setup_long >= 1 and not higher_bear:
        direction = "LONG"
    elif score <= 32 and short_votes >= max(2, n - 1) and setup_short >= 1 and not higher_bull:
        direction = "SHORT"
    else:
        return None
    preferred = ["15m", "5m", "1h"]
    plan = next((tf[t] for t in preferred if tf.get(t, {}).get("status") == "SETUP" and tf[t].get("direction") == direction), None)
    if not plan:
        return None
    return {"direction": direction, "score": round(score, 1), "plan": plan}


async def _historical(provider, symbol, timeframe, start, end):
    exchange, segment, _, groww_symbol = provider._instrument(symbol)
    params = {
        "exchange": exchange,
        "segment": segment,
        "groww_symbol": groww_symbol,
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
        "candle_interval": INTERVALS[timeframe],
    }
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.get(f"{provider.BASE_URL}/v1/historical/candles", headers=await provider._headers(), params=params)
    response.raise_for_status()
    payload = response.json().get("payload", response.json())
    return clean_candles(payload.get("candles", []))


def _simulate(plan, future):
    direction = plan["direction"]
    entry = float(plan["entry"])
    stop = float(plan["stop_loss"])
    t1 = float(plan["target1"])
    t2 = float(plan.get("target2") or t1)
    entry_day = str(plan["timestamp"])[:10]
    for c in future:
        if str(c[0])[:10] != entry_day:
            break
        high, low, close = float(c[2]), float(c[3]), float(c[4])
        if direction == "LONG":
            if low <= stop:
                return "SL", stop, (stop - entry) / (entry - stop if entry != stop else 1)
            if high >= t2:
                return "T2", t2, (t2 - entry) / (entry - stop)
            if high >= t1:
                return "T1", t1, (t1 - entry) / (entry - stop)
        else:
            if high >= stop:
                return "SL", stop, (entry - stop) / (stop - entry if stop != entry else 1)
            if low <= t2:
                return "T2", t2, (entry - t2) / (stop - entry)
            if low <= t1:
                return "T1", t1, (entry - t1) / (stop - entry)
    if future:
        last = next((x for x in reversed(future) if str(x[0])[:10] == entry_day), None)
        if last:
            exit_price = float(last[4])
            r = (exit_price - entry) / (entry - stop) if direction == "LONG" else (entry - exit_price) / (stop - entry)
            return "EOD", exit_price, r
    return "NO_EXIT", entry, 0.0


async def run_backtest(provider, symbols, start_date, end_date, min_rr=1.5):
    start = datetime.fromisoformat(start_date).replace(tzinfo=IST)
    end = datetime.fromisoformat(end_date).replace(tzinfo=IST) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 31:
        raise ValueError("Backtest range is limited to 31 days per run")
    warmup = start - timedelta(days=20)
    trades = []
    errors = []

    for symbol in symbols:
        symbol = symbol.upper().strip()
        try:
            data = {tf: await _historical(provider, symbol, tf, warmup, end) for tf in ("5m", "15m", "1h")}
            checkpoints = [c for c in data["15m"] if start <= (_ts(c[0]) or warmup) <= end]
            last_trade_day = None
            for cp in checkpoints:
                when = _ts(cp[0])
                if not when or when.hour < 9 or (when.hour == 9 and when.minute < 30) or when.hour >= 15:
                    continue
                day = when.date().isoformat()
                if last_trade_day == day:
                    continue
                tf = {}
                for timeframe in ("5m", "15m", "1h"):
                    history = [x for x in data[timeframe] if (_ts(x[0]) or when) <= when]
                    tf[timeframe] = analyze_candles(symbol, history[-260:], min_rr)
                mtf = _directional_mtf(tf)
                if not mtf:
                    continue
                plan = dict(mtf["plan"])
                rsi15 = float(tf["15m"].get("rsi14", 50))
                structure15 = str(tf["15m"].get("market_structure", ""))
                direction = mtf["direction"]
                aligned = sum(1 for t in ("5m", "15m", "1h") if tf[t].get("market_structure") == ("UPTREND" if direction == "LONG" else "DOWNTREND")) >= 2
                exhausted = rsi15 >= 80 if direction == "LONG" else rsi15 <= 20
                contradictory = structure15 == ("DOWNTREND" if direction == "LONG" else "UPTREND")
                if exhausted or contradictory or not aligned:
                    continue
                plan["direction"] = direction
                plan["timestamp"] = when.isoformat()
                future = [x for x in data["5m"] if (_ts(x[0]) or when) > when]
                outcome, exit_price, r_multiple = _simulate(plan, future)
                trades.append({
                    "symbol": symbol,
                    "timestamp": when.isoformat(),
                    "action": "BUY CE" if direction == "LONG" else "BUY PE",
                    "direction": direction,
                    "mtf_alpha": mtf["score"],
                    "entry": plan["entry"],
                    "stop_loss": plan["stop_loss"],
                    "target1": plan["target1"],
                    "target2": plan.get("target2"),
                    "underlying_rr": plan.get("risk_reward"),
                    "outcome": outcome,
                    "exit_price": round(exit_price, 2),
                    "r_multiple": round(r_multiple, 2),
                })
                last_trade_day = day
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    wins = sum(1 for t in trades if t["r_multiple"] > 0)
    losses = sum(1 for t in trades if t["r_multiple"] < 0)
    total_r = round(sum(t["r_multiple"] for t in trades), 2)
    equity = peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t["r_multiple"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "mode": "HISTORICAL_UNDERLYING_BACKTEST",
        "start_date": start_date,
        "end_date": end_date,
        "min_risk_reward": min_rr,
        "summary": {
            "trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / len(trades) * 100), 1) if trades else 0.0,
            "total_r": total_r,
            "average_r": round(total_r / len(trades), 2) if trades else 0.0,
            "max_drawdown_r": round(max_dd, 2),
        },
        "trades": trades,
        "errors": errors,
        "limitations": [
            "This is an underlying-price backtest of the scanner technical/MTF/safety logic.",
            "Historical option-chain premiums, IV, OI, Greeks, external news context and the live F&O confirmation score are not reconstructed.",
            "BUY CE/BUY PE labels indicate direction only; P&L is measured in underlying R-multiples, not option rupees.",
        ],
    }
