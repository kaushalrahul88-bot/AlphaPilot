from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean

HORIZONS = (1, 3, 6, 12, 24)  # 5/15/30/60/120 minutes on 5m bars


def _f(value, default=None):
    try:
        number = float(value)
        return number if isfinite(number) else default
    except (TypeError, ValueError):
        return default


def clean_ohlcv(candles):
    out = []
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        ts, o, h, l, c = row[:5]
        volume = _f(row[5], 0.0) if len(row) > 5 else 0.0
        oi = _f(row[6]) if len(row) > 6 else None
        o, h, l, c = map(_f, (o, h, l, c))
        if None in (o, h, l, c) or min(o, h, l, c) <= 0 or h < l:
            continue
        out.append([ts, o, h, l, c, max(volume or 0.0, 0.0), oi])
    return out


def _ema(values, period):
    if not values:
        return None
    k = 2.0 / (period + 1.0)
    value = values[0]
    for x in values[1:]:
        value = x * k + value * (1.0 - k)
    return value


def _atr(rows, end, period=14):
    start = max(1, end - period + 1)
    tr = []
    for i in range(start, end + 1):
        h, l, prev = rows[i][2], rows[i][3], rows[i - 1][4]
        tr.append(max(h - l, abs(h - prev), abs(l - prev)))
    return mean(tr) if tr else None


def _relative_volume(rows, end, period=20):
    history = [r[5] for r in rows[max(0, end - period):end] if r[5] > 0]
    if not history:
        return None
    base = mean(history)
    return rows[end][5] / base if base > 0 else None


def _structure(rows, end, lookback=20):
    sample = rows[max(0, end - lookback + 1):end + 1]
    if len(sample) < 8:
        return "UNKNOWN"
    half = len(sample) // 2
    a, b = sample[:half], sample[half:]
    ah, al = max(r[2] for r in a), min(r[3] for r in a)
    bh, bl = max(r[2] for r in b), min(r[3] for r in b)
    if bh > ah and bl > al:
        return "UPTREND"
    if bh < ah and bl < al:
        return "DOWNTREND"
    return "RANGE"


def _series_return(rows, end, bars):
    if end < bars or rows[end - bars][4] <= 0:
        return None
    return (rows[end][4] / rows[end - bars][4] - 1.0) * 100.0


def _aligned_return(context_rows, timestamp, bars=3):
    if not context_rows:
        return None
    clean = clean_ohlcv(context_rows)
    index = {str(r[0]): i for i, r in enumerate(clean)}
    i = index.get(str(timestamp))
    return _series_return(clean, i, bars) if i is not None else None


def build_copper_snapshot(mcx_candles, index, *, lme_candles=None, comex_candles=None, usdinr_candles=None):
    rows = clean_ohlcv(mcx_candles)
    if index < 50 or index >= len(rows):
        raise ValueError("Copper snapshot requires at least 50 completed MCX 5m bars")
    close = rows[index][4]
    closes = [r[4] for r in rows[:index + 1]]
    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
    atr = _atr(rows, index)
    oi_now = rows[index][6]
    oi_prev = rows[index - 3][6] if index >= 3 else None
    return {
        "timestamp": str(rows[index][0]),
        "price": close,
        "structure": _structure(rows, index),
        "return_15m_pct": _series_return(rows, index, 3),
        "return_60m_pct": _series_return(rows, index, 12),
        "ema20_gap_pct": (close / ema20 - 1.0) * 100.0 if ema20 else None,
        "ema50_gap_pct": (close / ema50 - 1.0) * 100.0 if ema50 else None,
        "atr_pct": atr / close * 100.0 if atr and close else None,
        "relative_volume": _relative_volume(rows, index),
        "oi_change_15m_pct": ((oi_now / oi_prev - 1.0) * 100.0) if oi_now is not None and oi_prev not in (None, 0) else None,
        "lme_return_15m_pct": _aligned_return(lme_candles, rows[index][0]),
        "comex_return_15m_pct": _aligned_return(comex_candles, rows[index][0]),
        "usdinr_return_15m_pct": _aligned_return(usdinr_candles, rows[index][0]),
    }


def label_forward_path(mcx_candles, index):
    rows = clean_ohlcv(mcx_candles)
    if index < 0 or index >= len(rows):
        raise IndexError(index)
    entry = rows[index][4]
    labels = {}
    for bars in HORIZONS:
        end = min(len(rows) - 1, index + bars)
        sample = rows[index + 1:end + 1]
        if not sample:
            labels[f"forward_{bars * 5}m_pct"] = None
            labels[f"mfe_{bars * 5}m_pct"] = None
            labels[f"mae_{bars * 5}m_pct"] = None
            continue
        labels[f"forward_{bars * 5}m_pct"] = (sample[-1][4] / entry - 1.0) * 100.0
        labels[f"mfe_{bars * 5}m_pct"] = (max(r[2] for r in sample) / entry - 1.0) * 100.0
        labels[f"mae_{bars * 5}m_pct"] = (min(r[3] for r in sample) / entry - 1.0) * 100.0
    return labels


def build_copper_experiences(mcx_candles, *, lme_candles=None, comex_candles=None, usdinr_candles=None, sample_every_bars=1):
    rows = clean_ohlcv(mcx_candles)
    step = max(1, int(sample_every_bars))
    experiences = []
    for i in range(50, max(50, len(rows) - max(HORIZONS)), step):
        snapshot = build_copper_snapshot(rows, i, lme_candles=lme_candles, comex_candles=comex_candles, usdinr_candles=usdinr_candles)
        experiences.append({"features": snapshot, "labels": label_forward_path(rows, i)})
    return experiences


def experiment_manifest():
    return {
        "mode": "ALPHAPILOT_COPPER_RESEARCH_BRAIN_V1",
        "research_only": True,
        "production_rules_changed": False,
        "bar_interval": "5m",
        "brains": {
            "A": ["mcx_price", "technical_baseline"],
            "B": ["mcx_price", "structure", "volume", "open_interest"],
            "C": ["brain_b", "lme_copper", "comex_copper", "usdinr"],
            "D": ["brain_c", "macro_event_context"],
        },
        "promotion_order": ["discovery", "candidate", "validated", "forward_test", "live_eligible"],
        "guardrails": [
            "No live strategy may self-modify from research output.",
            "Features use information available at the observation timestamp only.",
            "Forward labels are never inputs to the same observation.",
            "Chronological out-of-sample validation is required before promotion.",
            "Complexity must outperform the simpler baseline after realistic costs.",
        ],
    }
