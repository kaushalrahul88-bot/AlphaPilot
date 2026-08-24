from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean

from .backtest import _historical, _ts
from .option_native_phase2 import _clean_rows, _num

# Frozen before results: classic Nison-style candle families translated into
# deterministic intraday OHLC rules. Research only; never a production gate.
PATTERNS = (
    "HAMMER",
    "SHOOTING_STAR",
    "BULLISH_ENGULFING",
    "BEARISH_ENGULFING",
    "PIERCING",
    "DARK_CLOUD",
    "MORNING_STAR",
    "EVENING_STAR",
)


def _atr(rows: list[list], i: int, period: int = 14) -> float | None:
    vals = []
    for j in range(max(1, i - period + 1), i + 1):
        h, l, pc = _num(rows[j][2]), _num(rows[j][3]), _num(rows[j - 1][4])
        if None not in (h, l, pc):
            vals.append(max(h - l, abs(h - pc), abs(l - pc)))
    return mean(vals) if vals else None


def _parts(row: list) -> tuple[float, float, float, float, float, float, float] | None:
    o, h, l, c = (_num(row[x]) for x in (1, 2, 3, 4))
    if None in (o, h, l, c) or h <= l:
        return None
    body = abs(c - o); rng = h - l
    return o, h, l, c, body, h - max(o, c), min(o, c) - l


def _trend(rows: list[list], i: int, bars: int = 5) -> str:
    if i < bars: return "FLAT"
    a, b = _num(rows[i - bars][4]), _num(rows[i - 1][4])
    if not a or b is None: return "FLAT"
    r = b / a - 1.0
    return "UP" if r >= .003 else "DOWN" if r <= -.003 else "FLAT"


def _signals(rows: list[list], i: int) -> list[tuple[str, str]]:
    if i < 2: return []
    p, q, r = _parts(rows[i - 2]), _parts(rows[i - 1]), _parts(rows[i])
    if not p or not q or not r: return []
    po, ph, pl, pc, pb, pu, pd = p
    qo, qh, ql, qc, qb, qu, qd = q
    o, h, l, c, body, upper, lower = r
    rng = h - l; tr = _trend(rows, i)
    out = []
    # Umbrella/star lines require location context; body thresholds are frozen.
    if tr == "DOWN" and body <= .35 * rng and lower >= 2 * max(body, .01) and upper <= .25 * rng:
        out.append(("HAMMER", "LONG"))
    if tr == "UP" and body <= .35 * rng and upper >= 2 * max(body, .01) and lower <= .25 * rng:
        out.append(("SHOOTING_STAR", "SHORT"))
    if qc < qo and c > o and o <= qc and c >= qo:
        out.append(("BULLISH_ENGULFING", "LONG"))
    if qc > qo and c < o and o >= qc and c <= qo:
        out.append(("BEARISH_ENGULFING", "SHORT"))
    mid = (qo + qc) / 2
    if qc < qo and c > o and o <= qc and c >= mid and c < qo:
        out.append(("PIERCING", "LONG"))
    if qc > qo and c < o and o >= qc and c <= mid and c > qo:
        out.append(("DARK_CLOUD", "SHORT"))
    # Intraday star variants do not require literal exchange gaps; middle candle
    # must show contraction and third candle must reclaim >50% of first body.
    if pc < po and qb <= .35 * pb and c > o and c >= (po + pc) / 2:
        out.append(("MORNING_STAR", "LONG"))
    if pc > po and qb <= .35 * pb and c < o and c <= (po + pc) / 2:
        out.append(("EVENING_STAR", "SHORT"))
    return out


def _resolve(rows: list[list], signal_i: int, direction: str) -> float | None:
    if signal_i + 1 >= len(rows): return None
    entry = _num(rows[signal_i + 1][1]); atr = _atr(rows, signal_i)
    sig = _parts(rows[signal_i])
    if entry is None or atr is None or atr <= 0 or not sig: return None
    _, h, l, _, _, _, _ = sig
    if direction == "LONG":
        stop = min(l, entry - .6 * atr); risk = entry - stop; target = entry + risk
    else:
        stop = max(h, entry + .6 * atr); risk = stop - entry; target = entry - risk
    if risk <= 0: return None
    for row in rows[signal_i + 1:]:
        when = _ts(row[0]); hi, lo = _num(row[2]), _num(row[3])
        if not when or when.time() > time(15, 20) or hi is None or lo is None: break
        stop_hit = lo <= stop if direction == "LONG" else hi >= stop
        target_hit = hi >= target if direction == "LONG" else lo <= target
        if stop_hit and target_hit: return None  # exclude same-candle ambiguity
        if stop_hit: return -1.0
        if target_hit: return 1.0
    return None


async def run_candlestick_research(provider, symbols: list[str], start_date: str, end_date: str):
    start = datetime.fromisoformat(start_date); end = datetime.fromisoformat(end_date) + timedelta(hours=23, minutes=59)
    if end < start: raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 14: raise ValueError("Candlestick research is limited to 14 calendar days per block")
    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    grouped = defaultdict(list); errors = []
    for symbol in symbols:
        try: rows = _clean_rows(await _historical(provider, symbol, "5m", start, end))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{exc.__class__.__name__}: {exc}"}); continue
        for i in range(15, len(rows) - 1):
            when = _ts(rows[i][0])
            if not when or not (time(9, 45) <= when.time() <= time(14, 30)): continue
            for pattern, direction in _signals(rows, i):
                result = _resolve(rows, i, direction)
                if result is not None: grouped[(pattern, direction)].append(result)
    results = []
    for (pattern, direction), rs in sorted(grouped.items()):
        n = len(rs); wins = sum(x > 0 for x in rs); avg_r = mean(rs); pf = wins / max(1, n - wins)
        promising = n >= 12 and avg_r >= .10 and wins / n >= .55 and pf >= 1.20
        results.append({"pattern": pattern, "direction": direction, "trades": n, "win_rate_pct": round(wins/n*100,1), "avg_r": round(avg_r,3), "profit_factor": round(pf,2), "state": "PROMISING" if promising else "LOW_SAMPLE" if n < 12 else "WEAK"})
    return {"mode":"NISON_CANDLESTICK_DISCOVERY_V1","research_only":True,"production_rules_changed":False,"start_date":start_date,"end_date":end_date,"symbols":symbols,"frozen_patterns":list(PATTERNS),"results":results,"errors":errors,"gate":"PROMISING requires >=12 resolved trades, Avg R >= +0.10R, win rate >=55%, PF >=1.20. Replication must be evaluated unchanged across independent blocks before any OOS candidate."}
