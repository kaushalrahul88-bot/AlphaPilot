from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean

from .backtest import _historical, _ts
from .option_native_phase2 import run_option_native_phase2

REGIMES = ("TREND_LONG", "TREND_SHORT", "RANGE", "CHOP")


def _ema(values: list[float], period: int) -> float | None:
    if not values:
        return None
    alpha = 2.0 / (period + 1.0)
    value = float(values[0])
    for item in values[1:]:
        value = alpha * float(item) + (1.0 - alpha) * value
    return value


def _vwap(rows: list[list]) -> float | None:
    total_pv = 0.0
    total_v = 0.0
    for row in rows:
        try:
            high, low, close = float(row[2]), float(row[3]), float(row[4])
            volume = max(0.0, float(row[5])) if len(row) > 5 else 0.0
        except (TypeError, ValueError, IndexError):
            continue
        typical = (high + low + close) / 3.0
        total_pv += typical * volume
        total_v += volume
    if total_v > 0:
        return total_pv / total_v
    return float(rows[-1][4]) if rows else None


def _atr_pct(rows: list[list], period: int = 14) -> float | None:
    if len(rows) < 2:
        return None
    trs = []
    for i in range(1, len(rows)):
        try:
            high = float(rows[i][2]); low = float(rows[i][3]); prev = float(rows[i - 1][4])
        except (TypeError, ValueError, IndexError):
            continue
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    if not trs:
        return None
    atr = mean(trs[-period:])
    close = float(rows[-1][4])
    return atr / close * 100.0 if close > 0 else None


def _volume_ratio(rows: list[list], lookback: int = 8) -> float | None:
    if len(rows) < 2 or len(rows[-1]) <= 5:
        return None
    try:
        current = float(rows[-1][5])
        prior = [float(r[5]) for r in rows[max(0, len(rows) - lookback - 1):-1] if len(r) > 5 and float(r[5]) >= 0]
    except (TypeError, ValueError):
        return None
    avg = mean(prior) if prior else 0.0
    return current / avg if avg > 0 else None


def _day_slice(rows: list[list], when: datetime) -> list[list]:
    return [r for r in rows if (ts := _ts(r[0])) and ts.date() == when.date() and ts <= when]


def _return_from_open(rows: list[list]) -> float | None:
    if not rows:
        return None
    try:
        open_price = float(rows[0][1]); close = float(rows[-1][4])
    except (TypeError, ValueError, IndexError):
        return None
    return (close / open_price - 1.0) * 100.0 if open_price > 0 else None


def classify_market_brain(stock_rows: list[list], nifty_rows: list[list], when: datetime) -> dict:
    stock = _day_slice(stock_rows, when)
    nifty = _day_slice(nifty_rows, when)
    if len(stock) < 12:
        return {"final_regime": "CHOP", "trade_permission": "NO_TRADE", "alignment_score": 0, "reason": "INSUFFICIENT_HISTORY"}

    closes = [float(r[4]) for r in stock]
    close = closes[-1]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    vwap = _vwap(stock)
    stock_ret = _return_from_open(stock)
    nifty_ret = _return_from_open(nifty) if nifty else None
    relative_strength = (stock_ret - nifty_ret) if stock_ret is not None and nifty_ret is not None else None
    atr_pct = _atr_pct(stock)
    vr = _volume_ratio(stock)

    above_vwap = vwap is not None and close > vwap
    below_vwap = vwap is not None and close < vwap
    bullish_ema = ema20 is not None and ema50 is not None and close > ema20 > ema50
    bearish_ema = ema20 is not None and ema50 is not None and close < ema20 < ema50

    rs_state = "NEUTRAL"
    if relative_strength is not None:
        if relative_strength >= 0.20: rs_state = "STRONG"
        elif relative_strength <= -0.20: rs_state = "WEAK"

    volume_state = "NORMAL"
    if vr is not None:
        if vr >= 1.30: volume_state = "EXPANDING"
        elif vr <= 0.70: volume_state = "WEAK"

    volatility = "NORMAL"
    if atr_pct is not None:
        if atr_pct >= 0.55: volatility = "HIGH"
        elif atr_pct <= 0.18: volatility = "LOW"

    long_points = 0
    short_points = 0
    if above_vwap: long_points += 25
    if below_vwap: short_points += 25
    if bullish_ema: long_points += 30
    if bearish_ema: short_points += 30
    if rs_state == "STRONG": long_points += 25
    if rs_state == "WEAK": short_points += 25
    if stock_ret is not None and stock_ret >= 0.25: long_points += 10
    if stock_ret is not None and stock_ret <= -0.25: short_points += 10
    if nifty_ret is not None and nifty_ret >= 0.20: long_points += 10
    if nifty_ret is not None and nifty_ret <= -0.20: short_points += 10
    if volume_state == "EXPANDING":
        if long_points > short_points: long_points += 10
        elif short_points > long_points: short_points += 10

    alignment = max(long_points, short_points)
    directional_gap = abs(long_points - short_points)
    if long_points >= 65 and directional_gap >= 25:
        regime = "TREND_LONG"; permission = "CE"
    elif short_points >= 65 and directional_gap >= 25:
        regime = "TREND_SHORT"; permission = "PE"
    elif volatility == "LOW" or (directional_gap < 20 and alignment < 60):
        regime = "RANGE"; permission = "BOTH"
    else:
        regime = "CHOP"; permission = "NO_TRADE"

    return {
        "final_regime": regime,
        "trade_permission": permission,
        "alignment_score": int(alignment),
        "long_score": int(long_points),
        "short_score": int(short_points),
        "nifty_return_pct": round(nifty_ret, 3) if nifty_ret is not None else None,
        "stock_return_pct": round(stock_ret, 3) if stock_ret is not None else None,
        "relative_strength_pct": round(relative_strength, 3) if relative_strength is not None else None,
        "relative_strength_state": rs_state,
        "vwap_state": "ABOVE" if above_vwap else "BELOW" if below_vwap else "AT",
        "ema_structure": "BULLISH" if bullish_ema else "BEARISH" if bearish_ema else "MIXED",
        "volume_state": volume_state,
        "volume_ratio": round(vr, 2) if vr is not None else None,
        "volatility": volatility,
        "atr_pct": round(atr_pct, 3) if atr_pct is not None else None,
    }


def _summary(rows: list[dict]) -> dict:
    vals = [float(r["cost_adjusted_r"]) for r in rows if isinstance(r.get("cost_adjusted_r"), (int, float))]
    wins = sum(1 for x in vals if x > 0)
    gains = sum(x for x in vals if x > 0)
    losses = abs(sum(x for x in vals if x < 0))
    return {
        "trades": len(vals),
        "wins": wins,
        "win_rate": round(wins / len(vals) * 100.0, 1) if vals else 0.0,
        "average_r": round(sum(vals) / len(vals), 3) if vals else 0.0,
        "total_r": round(sum(vals), 3),
        "profit_factor": round(gains / losses, 3) if losses > 0 else None,
    }


async def run_market_regime_research(
    provider,
    symbols: list[str],
    start_date: str,
    end_date: str,
    premium_min_rr: float = 1.5,
    max_trades_per_model: int = 30,
    round_trip_cost_bps: float = 10.0,
):
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 14:
        raise ValueError("Market Regime Research v1 is limited to 14 calendar days per run")

    phase2 = await run_option_native_phase2(
        provider, symbols, start_date, end_date, premium_min_rr, max_trades_per_model, round_trip_cost_bps
    )
    try:
        nifty_rows = await _historical(provider, "NIFTY", "5m", start, end)
    except Exception:
        nifty_rows = []

    stock_cache: dict[str, list[list]] = {}
    errors = list(phase2.get("errors") or [])
    enriched_by_model: dict[str, list[dict]] = defaultdict(list)
    for model, trades in (phase2.get("trades_by_model") or {}).items():
        for trade in trades:
            symbol = str(trade.get("symbol") or "").upper()
            when = _ts(trade.get("signal_at"))
            if not symbol or not when:
                continue
            if symbol not in stock_cache:
                try:
                    stock_cache[symbol] = await _historical(provider, symbol, "5m", start, end)
                except Exception as exc:
                    errors.append({"symbol": symbol, "stage": "MARKET_BRAIN_UNDERLYING", "error": str(exc)})
                    stock_cache[symbol] = []
            brain = classify_market_brain(stock_cache[symbol], nifty_rows, when)
            enriched_by_model[model].append({**trade, "market_brain": brain})

    matrix = []
    for model, trades in enriched_by_model.items():
        for regime in REGIMES:
            subset = [t for t in trades if (t.get("market_brain") or {}).get("final_regime") == regime]
            if subset:
                matrix.append({"model": model, "regime": regime, **_summary(subset)})
    matrix.sort(key=lambda x: (x["average_r"], x["trades"]), reverse=True)

    permission_matrix = []
    for model, trades in enriched_by_model.items():
        for permission in ("CE", "PE", "BOTH", "NO_TRADE"):
            subset = [t for t in trades if (t.get("market_brain") or {}).get("trade_permission") == permission]
            if subset:
                permission_matrix.append({"model": model, "permission": permission, **_summary(subset)})

    return {
        "mode": "ALPHAPILOT_MARKET_REGIME_RESEARCH_V1",
        "research_only": True,
        "production_rules_changed": False,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": symbols,
        "definitions_frozen": True,
        "strategy_regime_matrix": matrix,
        "strategy_permission_matrix": permission_matrix,
        "trades_by_model": enriched_by_model,
        "phase2_leaderboard": phase2.get("leaderboard") or [],
        "errors": errors,
        "limitations": [
            "Market Brain v1 uses NIFTY + stock 5-minute data, not sector indices yet.",
            "Thresholds are frozen research definitions and are not optimized from the displayed results.",
            "The regime engine annotates existing Phase 2 option-native trades; it does not authorize live trades.",
            "Positive regime cells are hypotheses until they repeat on untouched symbols and dates.",
        ],
    }
