from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean

from .backtest import IST, _historical, _ts
from .strategy_research import _atr, _day_indices, _ema, _simulate_underlying, _vwap

SETUP_TYPES = (
    "COMPRESSION_EXPANSION",
    "VWAP_RECLAIM_REJECT",
    "PULLBACK_CONTINUATION",
    "FAILED_BREAKOUT_REVERSAL",
)

# Frozen development gates. These are descriptive research gates only.
MIN_BLOCK_TRADES = 12
AVG_R_GATE = 0.10
WIN_RATE_GATE = 55.0
PROFIT_FACTOR_GATE = 1.20


def _volume_ratio(rows: list[list], i: int, lookback: int = 20) -> float | None:
    start = max(0, i - lookback)
    prior = [float(rows[j][5]) for j in range(start, i) if len(rows[j]) > 5 and float(rows[j][5]) > 0]
    if not prior or len(rows[i]) <= 5:
        return None
    avg = mean(prior)
    return float(rows[i][5]) / avg if avg > 0 else None


def _compression_expansion(rows, indices, atrs):
    first = indices[0] if indices else 0
    search = [i for i in indices if (w := _ts(rows[i][0])) and time(9, 45) <= w.time() <= time(14, 15)]
    for i in search:
        if i - first < 12:
            continue
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        prior = list(range(i - 12, i))
        prior_high = max(float(rows[j][2]) for j in prior)
        prior_low = min(float(rows[j][3]) for j in prior)
        compression = (prior_high - prior_low) / atr
        if compression > 2.2:
            continue
        close = float(rows[i][4])
        vr = _volume_ratio(rows, i)
        if vr is not None and vr < 1.10:
            continue
        if close > prior_high + 0.10 * atr:
            stop = min(float(rows[i][3]), prior_high - 0.35 * atr)
            return i, "LONG", stop, {"compression_atr": round(compression, 2), "volume_ratio": round(vr, 2) if vr is not None else None}
        if close < prior_low - 0.10 * atr:
            stop = max(float(rows[i][2]), prior_low + 0.35 * atr)
            return i, "SHORT", stop, {"compression_atr": round(compression, 2), "volume_ratio": round(vr, 2) if vr is not None else None}
    return None


def _vwap_reclaim_reject(rows, indices, vwaps, ema20, ema50, atrs):
    search = [i for i in indices if (w := _ts(rows[i][0])) and time(9, 45) <= w.time() <= time(14, 15)]
    for i in search:
        if i < 2:
            continue
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        close = float(rows[i][4]); prev_close = float(rows[i - 1][4])
        low = float(rows[i][3]); high = float(rows[i][2])
        if ema20[i] > ema50[i] and prev_close < vwaps[i - 1] and close > vwaps[i] and close > ema20[i]:
            stop = min(low, vwaps[i] - 0.30 * atr)
            return i, "LONG", stop, {"vwap": round(vwaps[i], 2), "ema20": round(ema20[i], 2), "ema50": round(ema50[i], 2)}
        if ema20[i] < ema50[i] and prev_close > vwaps[i - 1] and close < vwaps[i] and close < ema20[i]:
            stop = max(high, vwaps[i] + 0.30 * atr)
            return i, "SHORT", stop, {"vwap": round(vwaps[i], 2), "ema20": round(ema20[i], 2), "ema50": round(ema50[i], 2)}
    return None


def _pullback_continuation(rows, indices, ema20, ema50, atrs):
    search = [i for i in indices if (w := _ts(rows[i][0])) and time(9, 45) <= w.time() <= time(14, 15)]
    for i in search:
        if i < 2:
            continue
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        prev_high, prev_low, prev_close = float(rows[i-1][2]), float(rows[i-1][3]), float(rows[i-1][4])
        close = float(rows[i][4]); high = float(rows[i][2]); low = float(rows[i][3])
        if ema20[i] > ema50[i] and prev_low <= ema20[i-1] <= prev_high and prev_close >= ema20[i-1] and close > prev_high:
            stop = min(prev_low, ema20[i] - 0.35 * atr)
            return i, "LONG", stop, {"ema20": round(ema20[i],2), "ema50": round(ema50[i],2), "trigger":"break_prev_high"}
        if ema20[i] < ema50[i] and prev_low <= ema20[i-1] <= prev_high and prev_close <= ema20[i-1] and close < prev_low:
            stop = max(prev_high, ema20[i] + 0.35 * atr)
            return i, "SHORT", stop, {"ema20": round(ema20[i],2), "ema50": round(ema50[i],2), "trigger":"break_prev_low"}
    return None


def _failed_breakout_reversal(rows, indices, atrs):
    first = indices[0] if indices else 0
    search = [i for i in indices if (w := _ts(rows[i][0])) and time(10, 0) <= w.time() <= time(14, 15)]
    for i in search:
        if i - first < 10:
            continue
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        prior = list(range(i - 10, i))
        prior_high = max(float(rows[j][2]) for j in prior)
        prior_low = min(float(rows[j][3]) for j in prior)
        high, low, close = float(rows[i][2]), float(rows[i][3]), float(rows[i][4])
        if low < prior_low - 0.10 * atr and close > prior_low:
            stop = low - 0.20 * atr
            return i, "LONG", stop, {"failed_level": round(prior_low,2), "sweep_atr": round((prior_low-low)/atr,2)}
        if high > prior_high + 0.10 * atr and close < prior_high:
            stop = high + 0.20 * atr
            return i, "SHORT", stop, {"failed_level": round(prior_high,2), "sweep_atr": round((high-prior_high)/atr,2)}
    return None


def _summary(trades: list[dict]):
    resolved = [t for t in trades if isinstance(t.get("r_multiple"), (int, float))]
    wins = [t for t in resolved if float(t["r_multiple"]) > 0]
    losses = [t for t in resolved if float(t["r_multiple"]) < 0]
    gross_win = sum(float(t["r_multiple"]) for t in wins)
    gross_loss = abs(sum(float(t["r_multiple"]) for t in losses))
    total = sum(float(t["r_multiple"]) for t in resolved)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    avg = total / len(resolved) if resolved else 0.0
    win_rate = len(wins) / len(resolved) * 100.0 if resolved else 0.0
    if len(resolved) < MIN_BLOCK_TRADES:
        state = "LOW_SAMPLE"
    elif avg >= AVG_R_GATE and win_rate >= WIN_RATE_GATE and pf >= PROFIT_FACTOR_GATE:
        state = "PROMISING"
    else:
        state = "WEAK"
    return {
        "trades": len(resolved), "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate,1), "average_r": round(avg,3), "total_r": round(total,2),
        "profit_factor": round(pf,2), "ambiguous": sum(t.get("outcome")=="AMBIGUOUS" for t in trades), "state": state,
    }


async def run_setup_discovery_v2(provider, symbols: list[str], start_date: str, end_date: str):
    # Local import avoids a module-load cycle: execution research deliberately reuses these frozen setup definitions.
    from .execution_structure_research import _break_confirmation, _pullback_entry, _summarize as _execution_summary

    start = datetime.fromisoformat(start_date).replace(tzinfo=IST)
    end = datetime.fromisoformat(end_date).replace(tzinfo=IST) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end-start).days > 16:
        raise ValueError("Setup Discovery v2 blocks are limited to 16 calendar days")

    by_setup: dict[str, list[dict]] = defaultdict(list)
    execution_records: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    errors = []
    for raw in symbols:
        symbol = raw.upper().strip()
        if not symbol:
            continue
        try:
            rows = await _historical(provider, symbol, "5m", start - timedelta(days=5), end)
            rows = [r for r in rows if _ts(r[0]) and _ts(r[0]) <= end]
            closes = [float(r[4]) for r in rows]
            atrs = _atr(rows, 14); vwaps = _vwap(rows); ema20 = _ema(closes,20); ema50 = _ema(closes,50)
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
                    sim = _simulate_underlying(rows, i, direction, float(stop), 1.0)
                    signal_at = _ts(rows[i][0])
                    base = {
                        "setup_type": setup_type, "symbol": symbol, "direction": direction,
                        "signal_at": signal_at.isoformat() if signal_at else str(rows[i][0]), "features": features,
                    }
                    if sim:
                        by_setup[setup_type].append({**base, "action": "BUY CE" if direction == "LONG" else "BUY PE", **sim})
                    methods = {
                        "NEXT_OPEN": sim,
                        "BREAK_CONFIRMATION": _break_confirmation(rows, i, direction, float(stop), float(atr)),
                        "PULLBACK_ENTRY": _pullback_entry(rows, i, direction, float(stop), float(atr)),
                    }
                    for method, method_sim in methods.items():
                        rec = dict(base); rec["filled"] = method_sim is not None
                        if method_sim:
                            rec.update(method_sim)
                        execution_records[(setup_type, direction, method)].append(rec)
        except Exception as exc:
            errors.append({"symbol":symbol,"error":str(exc)})

    rows = []
    execution_rows = []
    for setup_type in SETUP_TYPES:
        trades = by_setup.get(setup_type, [])
        for direction in ("LONG","SHORT"):
            sample = [t for t in trades if t.get("direction") == direction]
            rows.append({"setup_type":setup_type,"direction":direction, **_summary(sample)})
            control = _execution_summary(execution_records.get((setup_type, direction, "NEXT_OPEN"), []), "NEXT_OPEN")
            execution_rows.append({"setup_type":setup_type,"direction":direction,"method":"NEXT_OPEN",**control})
            for method in ("BREAK_CONFIRMATION","PULLBACK_ENTRY"):
                alt = _execution_summary(execution_records.get((setup_type, direction, method), []), method, control["average_r_per_signal"])
                execution_rows.append({"setup_type":setup_type,"direction":direction,"method":method,**alt})
    rows.sort(key=lambda x:(x["state"]=="PROMISING", x["average_r"], x["trades"]), reverse=True)
    execution_rows.sort(key=lambda x:(x["state"]=="IMPROVED", x.get("delta_vs_next_open") or -99, x["signals"]), reverse=True)
    return {
        "mode":"ALPHAPILOT_SETUP_DISCOVERY_V2",
        "research_only":True,"production_rules_changed":False,
        "start_date":start_date,"end_date":end_date,"symbols":symbols,
        "observations":sum(r["trades"] for r in rows),"rows":rows,"errors":errors,
        "execution_structure_v1": {
            "mode":"ALPHAPILOT_EXECUTION_STRUCTURE_DISCOVERY_V1",
            "rows":execution_rows,
            "fixed_methods":{
                "NEXT_OPEN":"Existing control entry at next 5m open.",
                "BREAK_CONFIRMATION":"Wait up to 3 bars for 0.05 ATR break beyond signal high/low; gap-through uses the worse open.",
                "PULLBACK_ENTRY":"Wait up to 3 bars for 0.25 ATR retracement from signal close; structural stop remains unchanged; stop-crossing fill bars are ambiguous/excluded.",
            },
            "fixed_improvement_gate":{"min_signals":12,"min_fill_rate":50.0,"delta_average_r_per_signal":0.10,"win_rate_filled":55.0,"profit_factor":1.20,"replication_blocks_required":3},
        },
        "fixed_gates":{"min_block_trades":MIN_BLOCK_TRADES,"average_r":AVG_R_GATE,"win_rate":WIN_RATE_GATE,"profit_factor":PROFIT_FACTOR_GATE,"target_r":1.0},
        "definitions":{
            "COMPRESSION_EXPANSION":"12-bar intraday range <=2.2 ATR, then >=0.10 ATR close outside the range with >=1.10x recent volume when volume is available.",
            "VWAP_RECLAIM_REJECT":"EMA20/EMA50 trend aligned cross back through session VWAP and EMA20.",
            "PULLBACK_CONTINUATION":"Trend-aligned EMA20 touch on the prior bar followed by a break of that bar in trend direction.",
            "FAILED_BREAKOUT_REVERSAL":"10-bar high/low liquidity sweep >=0.10 ATR followed by a close back inside the prior range; trade the rejection.",
        },
        "limitations":[
            "Underlying-price research only; BUY CE/BUY PE labels are directional and do not reconstruct option-premium P&L.",
            "Each setup definition is frozen before replication results and selects at most its first signal per symbol/day.",
            "A PROMISING block is not a production candidate. Exact setup type + direction must replicate across independent blocks before untouched OOS validation.",
            "Execution Structure v1 reuses the exact same frozen signal timestamps; unfilled alternatives count as 0R in average R per signal so participation loss is visible.",
            "News, cross-assets and Market Brain are intentionally excluded from this experiment.",
        ],
    }
