from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean

from .backtest import IST, _historical, _ts
from .strategy_research import _atr, _day_indices, _ema, _simulate_underlying, _vwap
from .setup_discovery_v2 import _summary, _volume_ratio

SETUP_TYPES = (
    "BREAKOUT_RETEST",
    "LIQUIDITY_SWEEP_CONFIRMATION",
    "TWO_BAR_MOMENTUM_EXPANSION",
    "VWAP_COMPRESSION_RELEASE",
)


def _breakout_retest(rows, indices, atrs):
    first = indices[0] if indices else 0
    for i in indices:
        stamp = _ts(rows[i][0])
        if not stamp or not time(9, 50) <= stamp.time() <= time(14, 15) or i - first < 13:
            continue
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        prior = range(i - 13, i - 1)
        high = max(float(rows[j][2]) for j in prior)
        low = min(float(rows[j][3]) for j in prior)
        breakout = rows[i - 1]
        close, bar_high, bar_low = float(rows[i][4]), float(rows[i][2]), float(rows[i][3])
        if float(breakout[4]) >= high + .10 * atr and bar_low <= high + .10 * atr and close > high:
            return i, "LONG", min(bar_low, high - .30 * atr), {"level": round(high, 2), "trigger": "breakout_retest_hold"}
        if float(breakout[4]) <= low - .10 * atr and bar_high >= low - .10 * atr and close < low:
            return i, "SHORT", max(bar_high, low + .30 * atr), {"level": round(low, 2), "trigger": "breakdown_retest_hold"}
    return None


def _liquidity_sweep_confirmation(rows, indices, atrs):
    first = indices[0] if indices else 0
    for i in indices:
        stamp = _ts(rows[i][0])
        if not stamp or not time(10, 0) <= stamp.time() <= time(14, 15) or i - first < 11:
            continue
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        prior = range(i - 11, i - 1)
        high = max(float(rows[j][2]) for j in prior)
        low = min(float(rows[j][3]) for j in prior)
        sweep = rows[i - 1]
        close = float(rows[i][4])
        if float(sweep[3]) < low - .10 * atr and float(sweep[4]) > low and close > float(sweep[2]):
            return i, "LONG", float(sweep[3]) - .15 * atr, {"level": round(low, 2), "trigger": "sweep_then_high_break"}
        if float(sweep[2]) > high + .10 * atr and float(sweep[4]) < high and close < float(sweep[3]):
            return i, "SHORT", float(sweep[2]) + .15 * atr, {"level": round(high, 2), "trigger": "sweep_then_low_break"}
    return None


def _two_bar_momentum_expansion(rows, indices, atrs, ema20):
    for i in indices:
        stamp = _ts(rows[i][0])
        if not stamp or not time(9, 45) <= stamp.time() <= time(14, 15) or i < 1:
            continue
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        a, b = rows[i - 1], rows[i]
        body_a, body_b = float(a[4]) - float(a[1]), float(b[4]) - float(b[1])
        combined = abs(body_a) + abs(body_b)
        vr = _volume_ratio(rows, i)
        if combined < 1.10 * atr or (vr is not None and vr < 1.10):
            continue
        if body_a > 0 and body_b > 0 and float(b[4]) > ema20[i]:
            return i, "LONG", min(float(a[3]), float(b[3])), {"body_atr": round(combined / atr, 2), "volume_ratio": round(vr, 2) if vr is not None else None}
        if body_a < 0 and body_b < 0 and float(b[4]) < ema20[i]:
            return i, "SHORT", max(float(a[2]), float(b[2])), {"body_atr": round(combined / atr, 2), "volume_ratio": round(vr, 2) if vr is not None else None}
    return None


def _vwap_compression_release(rows, indices, atrs, vwaps):
    first = indices[0] if indices else 0
    for i in indices:
        stamp = _ts(rows[i][0])
        if not stamp or not time(9, 50) <= stamp.time() <= time(14, 15) or i - first < 8:
            continue
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        prior = range(i - 8, i)
        high = max(float(rows[j][2]) for j in prior)
        low = min(float(rows[j][3]) for j in prior)
        if (high - low) > 1.60 * atr or not low <= vwaps[i - 1] <= high:
            continue
        close, vr = float(rows[i][4]), _volume_ratio(rows, i)
        if vr is not None and vr < 1.15:
            continue
        if close > high + .08 * atr:
            return i, "LONG", min(float(rows[i][3]), vwaps[i] - .25 * atr), {"range_atr": round((high-low)/atr, 2), "trigger": "vwap_range_high_release"}
        if close < low - .08 * atr:
            return i, "SHORT", max(float(rows[i][2]), vwaps[i] + .25 * atr), {"range_atr": round((high-low)/atr, 2), "trigger": "vwap_range_low_release"}
    return None


async def run_setup_discovery_v3(provider, symbols: list[str], start_date: str, end_date: str):
    start = datetime.fromisoformat(start_date).replace(tzinfo=IST)
    end = datetime.fromisoformat(end_date).replace(tzinfo=IST) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 7:
        raise ValueError("Setup Discovery v3 blocks are limited to 7 calendar days")
    if start.date() < datetime(2026, 4, 13).date() or end.date() > datetime(2026, 5, 22).date():
        raise ValueError("Setup Discovery v3 is frozen to 2026-04-13 through 2026-05-22 development dates")

    by_setup: dict[str, list[dict]] = defaultdict(list)
    errors = []
    for raw in symbols:
        symbol = raw.upper().strip()
        if not symbol:
            continue
        try:
            rows = await _historical(provider, symbol, "5m", start - timedelta(days=5), end)
            rows = [r for r in rows if _ts(r[0]) and _ts(r[0]) <= end]
            closes = [float(r[4]) for r in rows]
            atrs, vwaps, ema20 = _atr(rows, 14), _vwap(rows), _ema(closes, 20)
            for day, indices in sorted(_day_indices(rows).items()):
                d = datetime.fromisoformat(day).date()
                if d < start.date() or d > end.date():
                    continue
                signals = {
                    "BREAKOUT_RETEST": _breakout_retest(rows, indices, atrs),
                    "LIQUIDITY_SWEEP_CONFIRMATION": _liquidity_sweep_confirmation(rows, indices, atrs),
                    "TWO_BAR_MOMENTUM_EXPANSION": _two_bar_momentum_expansion(rows, indices, atrs, ema20),
                    "VWAP_COMPRESSION_RELEASE": _vwap_compression_release(rows, indices, atrs, vwaps),
                }
                for setup_type, signal in signals.items():
                    if not signal:
                        continue
                    i, direction, stop, features = signal
                    sim = _simulate_underlying(rows, i, direction, float(stop), 1.0)
                    if sim:
                        stamp = _ts(rows[i][0])
                        by_setup[setup_type].append({"setup_type": setup_type, "symbol": symbol, "direction": direction, "signal_at": stamp.isoformat() if stamp else str(rows[i][0]), "features": features, **sim})
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    result_rows = []
    for setup_type in SETUP_TYPES:
        for direction in ("LONG", "SHORT"):
            result_rows.append({"setup_type": setup_type, "direction": direction, **_summary([t for t in by_setup[setup_type] if t["direction"] == direction])})
    result_rows.sort(key=lambda x: (x["state"] == "PROMISING", x["average_r"], x["trades"]), reverse=True)
    return {
        "mode": "ALPHAPILOT_SETUP_DISCOVERY_V3_FAST_FOLLOW_THROUGH",
        "research_only": True, "production_rules_changed": False,
        "start_date": start_date, "end_date": end_date, "symbols": symbols,
        "observations": sum(row["trades"] for row in result_rows), "rows": result_rows, "errors": errors,
        "fixed_gates": {"min_block_trades": 12, "average_r": .10, "win_rate": 55.0, "profit_factor": 1.20, "target_r": 1.0, "replication_blocks_required": 4},
        "limitations": [
            "Underlying-price research only; no option-premium inference is permitted.",
            "Each frozen archetype selects at most its first signal per symbol/day and enters at the next 5-minute open.",
            "All six blocks precede Setup Discovery v2 and the rejected H-1; no H-1 diagnostic subgroup is used as a filter.",
            "A 4-of-6 replicated row is only a frozen candidate for a future untouched holdout after 2026-08-25.",
        ],
    }
