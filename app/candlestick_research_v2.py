from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean

from .backtest import IST, _historical, _ts
from .option_native_phase2 import _clean_rows, _num

PATTERNS = (
    "PIERCING",
    "DARK_CLOUD",
    "MORNING_STAR",
    "EVENING_STAR",
)

MIN_BLOCK_TRADES = 12
AVG_R_GATE = 0.10
WIN_RATE_GATE = 55.0
PROFIT_FACTOR_GATE = 1.20


def _atr(rows: list[list], i: int, period: int = 14) -> float | None:
    values: list[float] = []
    for j in range(max(1, i - period + 1), i + 1):
        high = _num(rows[j][2]); low = _num(rows[j][3]); prev_close = _num(rows[j - 1][4])
        if None in (high, low, prev_close):
            continue
        values.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return mean(values) if values else None


def _parts(row: list) -> tuple[float, float, float, float, float, float, float, float] | None:
    o, h, l, c = (_num(row[x]) for x in (1, 2, 3, 4))
    if None in (o, h, l, c) or h <= l:
        return None
    body = abs(c - o); rng = h - l
    upper = h - max(o, c); lower = min(o, c) - l
    return o, h, l, c, body, rng, upper, lower


def _trend(rows: list[list], i: int, bars: int = 5) -> str:
    if i < bars:
        return "FLAT"
    first = _num(rows[i - bars][4]); last = _num(rows[i - 1][4])
    if first is None or last is None or first <= 0:
        return "FLAT"
    change = last / first - 1.0
    if change <= -0.003:
        return "DOWN"
    if change >= 0.003:
        return "UP"
    return "FLAT"


def _signals(rows: list[list], i: int) -> list[tuple[str, str]]:
    if i < 2:
        return []
    a = _parts(rows[i - 2]); b = _parts(rows[i - 1]); cparts = _parts(rows[i])
    if not a or not b or not cparts:
        return []
    ao, ah, al, ac, ab, arng, au, ad = a
    bo, bh, bl, bc, bb, brng, bu, bd = b
    o, h, l, c, body, rng, upper, lower = cparts
    trend = _trend(rows, i)
    out: list[tuple[str, str]] = []

    prev_mid = (bo + bc) / 2.0
    if trend == "DOWN" and bc < bo and c > o and o <= bc and c >= prev_mid and c < bo:
        out.append(("PIERCING", "LONG"))
    if trend == "UP" and bc > bo and c < o and o >= bc and c <= prev_mid and c > bo:
        out.append(("DARK_CLOUD", "SHORT"))

    first_mid = (ao + ac) / 2.0
    # Intraday adaptation: no literal gap requirement. The middle candle must contract
    # to <=35% of the first real body and the third candle must reclaim >50% of it.
    if trend == "DOWN" and ac < ao and ab > 0 and bb <= 0.35 * ab and c > o and c >= first_mid:
        out.append(("MORNING_STAR", "LONG"))
    if trend == "UP" and ac > ao and ab > 0 and bb <= 0.35 * ab and c < o and c <= first_mid:
        out.append(("EVENING_STAR", "SHORT"))
    return out


def _resolve(rows: list[list], signal_i: int, direction: str) -> tuple[float | None, bool]:
    if signal_i + 1 >= len(rows):
        return None, False
    entry = _num(rows[signal_i + 1][1]); atr = _atr(rows, signal_i); sig = _parts(rows[signal_i])
    signal_when = _ts(rows[signal_i][0])
    if entry is None or atr is None or atr <= 0 or not sig or not signal_when:
        return None, False
    _, high, low, _, _, _, _, _ = sig
    if direction == "LONG":
        stop = min(low, entry - 0.60 * atr); risk = entry - stop; target = entry + risk
    else:
        stop = max(high, entry + 0.60 * atr); risk = stop - entry; target = entry - risk
    if risk <= 0:
        return None, False
    for row in rows[signal_i + 1:]:
        when = _ts(row[0]); hi = _num(row[2]); lo = _num(row[3])
        if not when or hi is None or lo is None:
            continue
        if when.date() != signal_when.date() or when.time() > time(15, 20):
            break
        stop_hit = lo <= stop if direction == "LONG" else hi >= stop
        target_hit = hi >= target if direction == "LONG" else lo <= target
        if stop_hit and target_hit:
            return None, True
        if stop_hit:
            return -1.0, False
        if target_hit:
            return 1.0, False
    return None, False


def _summary(values: list[float], ambiguous: int) -> dict:
    trades = len(values); wins = sum(v > 0 for v in values); losses = sum(v < 0 for v in values)
    total_r = sum(values); average_r = total_r / trades if trades else 0.0
    win_rate = wins / trades * 100.0 if trades else 0.0
    gross_win = sum(v for v in values if v > 0); gross_loss = abs(sum(v for v in values if v < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    if trades < MIN_BLOCK_TRADES:
        state = "LOW_SAMPLE"
    elif average_r >= AVG_R_GATE and win_rate >= WIN_RATE_GATE and pf >= PROFIT_FACTOR_GATE:
        state = "PROMISING"
    else:
        state = "WEAK"
    return {
        "trades": trades, "wins": wins, "losses": losses,
        "win_rate": round(win_rate, 1), "average_r": round(average_r, 3),
        "total_r": round(total_r, 2), "profit_factor": round(pf, 2),
        "ambiguous": ambiguous, "state": state,
    }


async def run_candlestick_research_v2(provider, symbols: list[str], start_date: str, end_date: str):
    start = datetime.fromisoformat(start_date).replace(tzinfo=IST)
    end = datetime.fromisoformat(end_date).replace(tzinfo=IST) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 14:
        raise ValueError("Candlestick Discovery v2 blocks are limited to 14 calendar days")

    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    ambiguous_counts: dict[tuple[str, str], int] = defaultdict(int)
    errors: list[dict] = []

    for symbol in symbols:
        try:
            rows = _clean_rows(await _historical(provider, symbol, "5m", start - timedelta(days=5), end))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{exc.__class__.__name__}: {exc}"})
            continue
        for i in range(15, len(rows) - 1):
            when = _ts(rows[i][0])
            if not when or when < start or when > end or not (time(9, 45) <= when.time() <= time(14, 30)):
                continue
            for pattern, direction in _signals(rows, i):
                value, ambiguous = _resolve(rows, i, direction)
                key = (pattern, direction)
                if ambiguous:
                    ambiguous_counts[key] += 1
                elif value is not None:
                    grouped[key].append(value)

    rows_out: list[dict] = []
    for pattern, direction in (
        ("PIERCING", "LONG"),
        ("DARK_CLOUD", "SHORT"),
        ("MORNING_STAR", "LONG"),
        ("EVENING_STAR", "SHORT"),
    ):
        key = (pattern, direction)
        rows_out.append({"pattern_type": pattern, "direction": direction, **_summary(grouped.get(key, []), ambiguous_counts.get(key, 0))})
    rows_out.sort(key=lambda x: (x["state"] == "PROMISING", x["average_r"], x["trades"]), reverse=True)

    return {
        "mode": "ALPHAPILOT_CANDLESTICK_DISCOVERY_V2",
        "research_only": True,
        "production_rules_changed": False,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": symbols,
        "observations": sum(r["trades"] for r in rows_out),
        "rows": rows_out,
        "errors": errors,
        "fixed_gates": {"min_block_trades": MIN_BLOCK_TRADES, "average_r": AVG_R_GATE, "win_rate": WIN_RATE_GATE, "profit_factor": PROFIT_FACTOR_GATE, "target_r": 1.0},
        "definitions": {
            "PIERCING": "After a fixed 5-bar decline >=0.3%, current bullish candle opens at/below prior close and closes through at least 50% of the prior bearish body without fully engulfing it.",
            "DARK_CLOUD": "After a fixed 5-bar rise >=0.3%, current bearish candle opens at/above prior close and closes through at least 50% of the prior bullish body without fully engulfing it.",
            "MORNING_STAR": "After a fixed 5-bar decline, bearish first candle, middle real body <=35% of first body, then bullish third candle closes through at least 50% of first body. No literal intraday gap required.",
            "EVENING_STAR": "After a fixed 5-bar rise, bullish first candle, middle real body <=35% of first body, then bearish third candle closes through at least 50% of first body. No literal intraday gap required.",
        },
        "limitations": [
            "Underlying-price research only; no option-premium P&L is reconstructed here.",
            "These four v2 patterns were frozen before any v2 result was observed.",
            "Signals enter at the next 5-minute open with the same frozen structure/0.60 ATR stop and fixed 1R target used in v1.",
            "Same-candle target plus stop ambiguity is excluded from resolved statistics.",
            "A replicated v2 pattern still requires untouched OOS validation before option-premium or production consideration.",
        ],
    }
