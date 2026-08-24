from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta

from .backtest import IST, _historical, _ts
from .strategy_research import _atr, _day_indices, _ema, _simulate_underlying, _vwap
from .setup_discovery_v2 import (
    SETUP_TYPES,
    _compression_expansion,
    _failed_breakout_reversal,
    _pullback_continuation,
    _vwap_reclaim_reject,
)

METHODS = ("NEXT_OPEN", "BREAK_CONFIRMATION", "PULLBACK_ENTRY")
MIN_BLOCK_SIGNALS = 12
MIN_FILL_RATE = 50.0
DELTA_R_GATE = 0.10
PF_GATE = 1.20
WIN_RATE_GATE = 55.0
LOOKAHEAD_BARS = 3
BREAK_BUFFER_ATR = 0.05
PULLBACK_ATR = 0.25


def _profit_factor(rs: list[float]) -> float:
    gross_win = sum(x for x in rs if x > 0)
    gross_loss = abs(sum(x for x in rs if x < 0))
    return gross_win / gross_loss if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)


def _simulate_from_entry(rows, entry_i: int, direction: str, entry: float, stop: float, target_r: float = 1.0):
    risk = (entry - stop) if direction == "LONG" else (stop - entry)
    if risk <= 0:
        return None
    target = entry + target_r * risk if direction == "LONG" else entry - target_r * risk
    for j in range(entry_i, min(len(rows), entry_i + 79)):
        high, low = float(rows[j][2]), float(rows[j][3])
        hit_target = high >= target if direction == "LONG" else low <= target
        hit_stop = low <= stop if direction == "LONG" else high >= stop
        if hit_target and hit_stop:
            return {"outcome": "AMBIGUOUS", "r_multiple": None, "entry": entry, "stop": stop, "target": target, "exit_i": j}
        if hit_stop:
            return {"outcome": "STOP", "r_multiple": -1.0, "entry": entry, "stop": stop, "target": target, "exit_i": j}
        if hit_target:
            return {"outcome": "TARGET", "r_multiple": target_r, "entry": entry, "stop": stop, "target": target, "exit_i": j}
    return {"outcome": "TIMEOUT", "r_multiple": 0.0, "entry": entry, "stop": stop, "target": target, "exit_i": min(len(rows) - 1, entry_i + 78)}


def _next_open(rows, signal_i: int, direction: str, stop: float):
    return _simulate_underlying(rows, signal_i, direction, float(stop), 1.0)


def _break_confirmation(rows, signal_i: int, direction: str, stop: float, atr: float):
    trigger = float(rows[signal_i][2]) + BREAK_BUFFER_ATR * atr if direction == "LONG" else float(rows[signal_i][3]) - BREAK_BUFFER_ATR * atr
    for j in range(signal_i + 1, min(len(rows), signal_i + 1 + LOOKAHEAD_BARS)):
        o, h, l = float(rows[j][1]), float(rows[j][2]), float(rows[j][3])
        touched = h >= trigger if direction == "LONG" else l <= trigger
        if not touched:
            continue
        # Conservative stop-entry fill: if the bar opens beyond the trigger, use the worse open.
        entry = max(o, trigger) if direction == "LONG" else min(o, trigger)
        return _simulate_from_entry(rows, j, direction, entry, float(stop), 1.0)
    return None


def _pullback_entry(rows, signal_i: int, direction: str, stop: float, atr: float):
    anchor = float(rows[signal_i][4])
    limit = anchor - PULLBACK_ATR * atr if direction == "LONG" else anchor + PULLBACK_ATR * atr
    for j in range(signal_i + 1, min(len(rows), signal_i + 1 + LOOKAHEAD_BARS)):
        o, h, l = float(rows[j][1]), float(rows[j][2]), float(rows[j][3])
        touched = l <= limit if direction == "LONG" else h >= limit
        if not touched:
            continue
        # Conservative limit fill: favorable gaps use the requested limit, not the better open.
        entry = limit
        # If price crossed the structural stop before/while reaching the limit, reject the fill rather than fabricate chronology.
        stop_crossed = l <= stop if direction == "LONG" else h >= stop
        if stop_crossed:
            return {"outcome": "AMBIGUOUS_PREENTRY", "r_multiple": None, "entry": entry, "stop": stop, "target": None, "exit_i": j}
        return _simulate_from_entry(rows, j, direction, entry, float(stop), 1.0)
    return None


def _summarize(records: list[dict], method: str, control_avg_per_signal: float | None = None):
    signals = len(records)
    fills = [r for r in records if r.get("filled")]
    resolved = [r for r in fills if isinstance(r.get("r_multiple"), (int, float))]
    rs = [float(r["r_multiple"]) for r in resolved]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    total_r = sum(rs)
    avg_filled = total_r / len(resolved) if resolved else 0.0
    avg_per_signal = total_r / signals if signals else 0.0
    win_rate = len(wins) / len(resolved) * 100 if resolved else 0.0
    fill_rate = len(fills) / signals * 100 if signals else 0.0
    pf = _profit_factor(rs)
    delta = None if control_avg_per_signal is None else avg_per_signal - control_avg_per_signal
    state = "CONTROL" if method == "NEXT_OPEN" else "WEAK"
    if method != "NEXT_OPEN":
        if signals < MIN_BLOCK_SIGNALS:
            state = "LOW_SAMPLE"
        elif fill_rate >= MIN_FILL_RATE and delta is not None and delta >= DELTA_R_GATE and pf >= PF_GATE and win_rate >= WIN_RATE_GATE:
            state = "IMPROVED"
    return {
        "signals": signals,
        "fills": len(fills),
        "resolved": len(resolved),
        "fill_rate": round(fill_rate, 1),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "average_r_filled": round(avg_filled, 3),
        "average_r_per_signal": round(avg_per_signal, 3),
        "total_r": round(total_r, 2),
        "profit_factor": round(pf, 2),
        "delta_vs_next_open": round(delta, 3) if delta is not None else None,
        "ambiguous": sum(str(r.get("outcome", "")).startswith("AMBIGUOUS") for r in fills),
        "state": state,
    }


async def run_execution_structure_research(provider, symbols: list[str], start_date: str, end_date: str):
    start = datetime.fromisoformat(start_date).replace(tzinfo=IST)
    end = datetime.fromisoformat(end_date).replace(tzinfo=IST) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 16:
        raise ValueError("Execution Structure Discovery v1 blocks are limited to 16 calendar days")

    records: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    errors = []
    for raw in symbols:
        symbol = raw.upper().strip()
        if not symbol:
            continue
        try:
            rows = await _historical(provider, symbol, "5m", start - timedelta(days=5), end)
            rows = [r for r in rows if _ts(r[0]) and _ts(r[0]) <= end]
            closes = [float(r[4]) for r in rows]
            atrs = _atr(rows, 14)
            vwaps = _vwap(rows)
            ema20 = _ema(closes, 20)
            ema50 = _ema(closes, 50)
            for day, indices in sorted(_day_indices(rows).items()):
                d = datetime.fromisoformat(day).date()
                if d < start.date() or d > end.date():
                    continue
                definitions = {
                    "COMPRESSION_EXPANSION": _compression_expansion(rows, indices, atrs),
                    "VWAP_RECLAIM_REJECT": _vwap_reclaim_reject(rows, indices, vwaps, ema20, ema50, atrs),
                    "PULLBACK_CONTINUATION": _pullback_continuation(rows, indices, ema20, ema50, atrs),
                    "FAILED_BREAKOUT_REVERSAL": _failed_breakout_reversal(rows, indices, atrs),
                }
                for setup_type, signal in definitions.items():
                    if not signal:
                        continue
                    i, direction, stop, features = signal
                    atr = atrs[i] if i < len(atrs) else 0.0
                    if atr <= 0:
                        continue
                    signal_at = _ts(rows[i][0])
                    base = {"setup_type": setup_type, "symbol": symbol, "direction": direction, "signal_at": signal_at.isoformat() if signal_at else str(rows[i][0]), "features": features}
                    sims = {
                        "NEXT_OPEN": _next_open(rows, i, direction, float(stop)),
                        "BREAK_CONFIRMATION": _break_confirmation(rows, i, direction, float(stop), float(atr)),
                        "PULLBACK_ENTRY": _pullback_entry(rows, i, direction, float(stop), float(atr)),
                    }
                    for method, sim in sims.items():
                        rec = dict(base)
                        rec["filled"] = sim is not None
                        if sim:
                            rec.update(sim)
                        records[(setup_type, direction, method)].append(rec)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    out = []
    for setup_type in SETUP_TYPES:
        for direction in ("LONG", "SHORT"):
            control_records = records.get((setup_type, direction, "NEXT_OPEN"), [])
            control = _summarize(control_records, "NEXT_OPEN")
            out.append({"setup_type": setup_type, "direction": direction, "method": "NEXT_OPEN", **control})
            for method in ("BREAK_CONFIRMATION", "PULLBACK_ENTRY"):
                summary = _summarize(records.get((setup_type, direction, method), []), method, control["average_r_per_signal"])
                out.append({"setup_type": setup_type, "direction": direction, "method": method, **summary})
    out.sort(key=lambda x: (x["state"] == "IMPROVED", x.get("delta_vs_next_open") or -99, x["signals"]), reverse=True)
    return {
        "mode": "ALPHAPILOT_EXECUTION_STRUCTURE_DISCOVERY_V1",
        "research_only": True,
        "production_rules_changed": False,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": symbols,
        "rows": out,
        "errors": errors,
        "fixed_execution_methods": {
            "NEXT_OPEN": "Control: existing Setup Discovery v2 next-5m-open entry with the original frozen structural stop and fixed 1R target.",
            "BREAK_CONFIRMATION": f"Wait up to {LOOKAHEAD_BARS} bars for a stop-entry {BREAK_BUFFER_ATR:.2f} ATR beyond the signal candle high/low; worse open is used on gap-through.",
            "PULLBACK_ENTRY": f"Wait up to {LOOKAHEAD_BARS} bars for a {PULLBACK_ATR:.2f} ATR retracement from signal close; structural stop is unchanged; bars that also cross the stop are excluded as pre-entry ambiguous.",
        },
        "fixed_improvement_gate": {
            "min_signals": MIN_BLOCK_SIGNALS,
            "min_fill_rate": MIN_FILL_RATE,
            "delta_average_r_per_signal": DELTA_R_GATE,
            "win_rate_filled": WIN_RATE_GATE,
            "profit_factor": PF_GATE,
            "replication_blocks_required": 3,
        },
        "limitations": [
            "This is execution-structure research on the same frozen Setup Discovery v2 signals; it does not invent new signal filters.",
            "Average R per signal treats unfilled alternative entries as 0R, so lower participation is not hidden by reporting only filled trades.",
            "Same-bar target/stop ambiguity and pullback bars that also cross the stop are excluded rather than assigned favorable chronology.",
            "An IMPROVED block is research evidence only. Exact setup type + direction + execution method must improve in at least three independent blocks before untouched OOS.",
            "Production scanner, option confirmation, risk and execution rules are unchanged.",
        ],
    }
