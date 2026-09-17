from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from math import isfinite
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp

IST = ZoneInfo("Asia/Kolkata")
HORIZONS = (1, 3, 6, 12, 24)  # 5/15/30/60/120 minutes on 5m bars
BAR_MINUTES = 5


def _f(value, default=None):
    try:
        number = float(value)
        return number if isfinite(number) else default
    except (TypeError, ValueError):
        return default


def clean_ohlcv(candles):
    rows = []
    for row in candles or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = parse_ist_timestamp(row[0])
            o, h, l, c = map(float, row[1:5])
            volume = max(0.0, float(row[5] or 0.0)) if len(row) > 5 else 0.0
            oi = float(row[6]) if len(row) > 6 and row[6] is not None else None
        except (TypeError, ValueError, OverflowError):
            continue
        if min(o, h, l, c) <= 0 or h < l:
            continue
        rows.append([stamp, o, h, l, c, volume, oi])
    rows.sort(key=lambda x: x[0])
    # Fail closed on duplicate timestamps rather than mixing overlapping contracts.
    deduped = []
    for row in rows:
        if deduped and row[0] == deduped[-1][0]:
            raise ValueError("Duplicate Crude candle timestamp; exact-contract data is required")
        deduped.append(row)
    return deduped


def _ema(values, period):
    if not values:
        return None
    k = 2.0 / (period + 1.0)
    value = values[0]
    for x in values[1:]:
        value = x * k + value * (1.0 - k)
    return value


def _atr(rows, index, period=14):
    start = max(1, index - period + 1)
    values = []
    for i in range(start, index + 1):
        high, low, previous_close = rows[i][2], rows[i][3], rows[i - 1][4]
        values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return mean(values) if values else None


def _relative_volume(rows, index, period=20):
    history = [r[5] for r in rows[max(0, index - period):index] if r[5] > 0]
    if not history:
        return None
    baseline = mean(history)
    return rows[index][5] / baseline if baseline > 0 else None


def _series_return(rows, index, bars):
    if index < bars or rows[index - bars][4] <= 0:
        return None
    return (rows[index][4] / rows[index - bars][4] - 1.0) * 100.0


def _structure(rows, index, lookback=20):
    sample = rows[max(0, index - lookback + 1):index + 1]
    if len(sample) < 8:
        return "UNKNOWN"
    half = len(sample) // 2
    first, second = sample[:half], sample[half:]
    first_high, first_low = max(r[2] for r in first), min(r[3] for r in first)
    second_high, second_low = max(r[2] for r in second), min(r[3] for r in second)
    if second_high > first_high and second_low > first_low:
        return "UPTREND"
    if second_high < first_high and second_low < first_low:
        return "DOWNTREND"
    return "RANGE"


def _session_context(rows, index):
    day = rows[index][0].astimezone(IST).date()
    session = [r for r in rows[:index + 1] if r[0].astimezone(IST).date() == day]
    if not session:
        return {}
    close = rows[index][4]
    opening = session[0][1]
    high = max(r[2] for r in session)
    low = min(r[3] for r in session)
    span = high - low
    pv = sum(((r[2] + r[3] + r[4]) / 3.0) * r[5] for r in session if r[5] > 0)
    volume = sum(r[5] for r in session if r[5] > 0)
    vwap = pv / volume if volume > 0 else None
    return {
        "session_return_pct": (close / opening - 1.0) * 100.0 if opening > 0 else None,
        "session_range_position": (close - low) / span if span > 0 else 0.5,
        "session_range_pct": span / close * 100.0 if close > 0 else None,
        "session_vwap_gap_pct": (close / vwap - 1.0) * 100.0 if vwap and vwap > 0 else None,
    }


def _oi_state(rows, index):
    now = rows[index][6]
    previous = rows[index - 3][6] if index >= 3 else None
    if now is None or previous in (None, 0):
        return {"oi_change_15m_pct": None, "price_oi_state": "UNKNOWN"}
    oi_change = (now / previous - 1.0) * 100.0
    price_change = _series_return(rows, index, 3)
    if price_change is None:
        state = "UNKNOWN"
    elif price_change > 0 and oi_change > 0:
        state = "LONG_BUILDUP"
    elif price_change < 0 and oi_change > 0:
        state = "SHORT_BUILDUP"
    elif price_change > 0 and oi_change < 0:
        state = "SHORT_COVERING"
    elif price_change < 0 and oi_change < 0:
        state = "LONG_UNWINDING"
    else:
        state = "FLAT"
    return {"oi_change_15m_pct": oi_change, "price_oi_state": state}


def _build_snapshot_clean(rows, index):
    if index < 50 or index >= len(rows):
        raise ValueError("Crude snapshot requires at least 50 completed MCX 5m bars")
    close = rows[index][4]
    closes = [r[4] for r in rows[:index + 1]]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    atr = _atr(rows, index)
    bar_start = rows[index][0]
    return {
        "bar_start": bar_start.isoformat(),
        "available_at": (bar_start + timedelta(minutes=BAR_MINUTES)).isoformat(),
        "price": close,
        "structure": _structure(rows, index),
        "return_15m_pct": _series_return(rows, index, 3),
        "return_60m_pct": _series_return(rows, index, 12),
        "ema20_gap_pct": (close / ema20 - 1.0) * 100.0 if ema20 else None,
        "ema50_gap_pct": (close / ema50 - 1.0) * 100.0 if ema50 else None,
        "atr_pct": atr / close * 100.0 if atr and close else None,
        "relative_volume": _relative_volume(rows, index),
        **_session_context(rows, index),
        **_oi_state(rows, index),
    }


def build_crude_snapshot(mcx_candles, index):
    return _build_snapshot_clean(clean_ohlcv(mcx_candles), index)


def _label_forward_path_clean(rows, index):
    entry = rows[index][4]
    labels = {}
    for bars in HORIZONS:
        end = min(len(rows) - 1, index + bars)
        future = rows[index + 1:end + 1]
        minutes = bars * BAR_MINUTES
        if not future:
            labels[f"forward_{minutes}m_pct"] = None
            labels[f"mfe_{minutes}m_pct"] = None
            labels[f"mae_{minutes}m_pct"] = None
            continue
        labels[f"forward_{minutes}m_pct"] = (future[-1][4] / entry - 1.0) * 100.0
        labels[f"mfe_{minutes}m_pct"] = (max(r[2] for r in future) / entry - 1.0) * 100.0
        labels[f"mae_{minutes}m_pct"] = (min(r[3] for r in future) / entry - 1.0) * 100.0
    return labels


def build_crude_experiences(mcx_candles, sample_every_bars=3):
    rows = clean_ohlcv(mcx_candles)
    step = max(1, int(sample_every_bars))
    experiences = []
    last_index = max(50, len(rows) - max(HORIZONS))
    for index in range(50, last_index, step):
        experiences.append({
            "features": _build_snapshot_clean(rows, index),
            "labels": _label_forward_path_clean(rows, index),
        })
    return experiences


def brain_a_signal(features):
    """Frozen Crude Experiment-001 technical baseline; NEWS is forbidden."""
    structure = str(features.get("structure") or "UNKNOWN")
    ret15 = _f(features.get("return_15m_pct"), 0.0)
    ema20_gap = _f(features.get("ema20_gap_pct"), 0.0)
    ema50_gap = _f(features.get("ema50_gap_pct"), 0.0)
    if structure == "UPTREND" and ret15 > 0 and ema20_gap > 0 and ema50_gap > 0:
        return "BUY"
    if structure == "DOWNTREND" and ret15 < 0 and ema20_gap < 0 and ema50_gap < 0:
        return "SELL"
    return "NO_TRADE"


def evaluate_brain_a(experiences, horizon_minutes=60, round_trip_cost_bps=4.0):
    key = f"forward_{int(horizon_minutes)}m_pct"
    decisions = []
    for item in experiences:
        features = item.get("features") or {}
        forward = _f((item.get("labels") or {}).get(key))
        signal = brain_a_signal(features)
        if signal == "NO_TRADE" or forward is None:
            continue
        gross = forward if signal == "BUY" else -forward
        net = gross - max(0.0, float(round_trip_cost_bps)) / 100.0
        decisions.append({
            "available_at": features.get("available_at"),
            "signal": signal,
            "gross_pct": gross,
            "net_pct": net,
        })
    values = [x["net_pct"] for x in decisions]
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "brain": "A",
        "name": "CRUDE_MCX_TECHNICAL_BASELINE",
        "research_only": True,
        "news_enabled": False,
        "horizon_minutes": int(horizon_minutes),
        "round_trip_cost_bps": float(round_trip_cost_bps),
        "signals": len(decisions),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(decisions) * 100.0, 2) if decisions else 0.0,
        "avg_net_return_pct": round(mean(values), 4) if values else 0.0,
        "net_return_sum_pct": round(sum(values), 4),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "decisions": decisions[-250:],
        "rules_frozen": {
            "buy": "UPTREND + positive 15m return + price above EMA20 and EMA50",
            "sell": "DOWNTREND + negative 15m return + price below EMA20 and EMA50",
        },
    }


def chronological_split(experiences, train_fraction=0.70):
    if len(experiences) < 20:
        return experiences[:], []
    cut = max(1, min(len(experiences) - 1, int(len(experiences) * float(train_fraction))))
    return experiences[:cut], experiences[cut:]


def experiment_manifest():
    return {
        "mode": "ALPHAPILOT_CRUDE_RESEARCH_BRAIN_V1",
        "commodity": "CRUDEOIL",
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "bar_timing": "GROWW_TIMESTAMP_IS_BAR_START; FEATURES_VISIBLE_AT_BAR_START_PLUS_5_MINUTES",
        "news_enabled": False,
        "news_policy": "FORBIDDEN_IN_BASELINE",
        "path": [
            "EXPERIMENT_001_TECHNICAL_BASELINE",
            "STRUCTURE_PARTICIPATION_BRAIN",
            "EDGE_ATTRIBUTION",
            "REGIME_STABILITY",
            "DAY_BY_DAY_REPLAY",
            "RANDOM_CLICK_REPLAY",
            "NO_NEWS_CURRENT_MIND",
            "FREEZE_NO_NEWS_BASELINE",
            "ADD_POINT_IN_TIME_NEWS_INTELLIGENCE",
            "SAME_CLICK_COMPARISON",
        ],
        "guardrails": [
            "No news, headlines, event labels or outcome-derived context may enter Experiment 001 decisions.",
            "Only an exact MCX Crude contract series may be scored; duplicate timestamps fail closed.",
            "A 5-minute bar is usable only at timestamp plus five minutes.",
            "Forward labels are attached only after the decision snapshot is frozen.",
            "The future news comparison must reuse the same dates and click timestamps as the frozen no-news baseline.",
        ],
    }


def run_crude_experiment_001(candles, *, trading_symbol, sample_every_bars=3, round_trip_cost_bps=4.0):
    rows = clean_ohlcv(candles)
    if len(rows) < 80:
        raise RuntimeError(f"Insufficient exact-contract MCX Crude 5m history ({len(rows)} candles)")
    experiences = build_crude_experiences(rows, sample_every_bars=sample_every_bars)
    train, holdout = chronological_split(experiences, 0.70)
    return {
        "mode": "ALPHAPILOT_CRUDE_EXPERIMENT_001",
        "commodity": "CRUDEOIL",
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "news_enabled": False,
        "trading_symbol": str(trading_symbol),
        "sample_every_bars": max(1, int(sample_every_bars)),
        "coverage": {
            "mcx_5m_candles": len(rows),
            "experiences": len(experiences),
            "start_bar": rows[0][0].isoformat(),
            "end_bar": rows[-1][0].isoformat(),
        },
        "full_sample": evaluate_brain_a(experiences, 60, round_trip_cost_bps),
        "chronological_split": {
            "train_experiences": len(train),
            "holdout_experiences": len(holdout),
            "train": evaluate_brain_a(train, 60, round_trip_cost_bps),
            "holdout": evaluate_brain_a(holdout, 60, round_trip_cost_bps),
        },
        "manifest": experiment_manifest(),
        "next_gate": "Freeze Brain A, then build the no-news structure/participation Brain B and compare on the same chronological holdout.",
        "limitations": [
            "Experiment 001 is the starting technical baseline and does not yet represent the final Crude Current Mind.",
            "This result is underlying-futures direction research, not option-premium P&L.",
            "A single currently-listed contract may provide extended historical candles; that is acceptable for this starting baseline but is not proof of a valid continuous front-month history.",
        ],
    }


def _read_exact_contract_sync(database_url, trading_symbol, start, end):
    import psycopg

    sql = """
        SELECT candle_at, open, high, low, close, volume, open_interest
        FROM commodity_candles
        WHERE provider = 'GROWW' AND symbol = 'CRUDEOIL'
          AND trading_symbol = %s AND timeframe_minutes = 5
          AND candle_at >= %s AND candle_at <= %s
        ORDER BY candle_at ASC
    """
    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (trading_symbol, start, end))
            data = cursor.fetchall()
    return [
        [stamp.isoformat(), float(o), float(h), float(l), float(c), float(v or 0), float(oi) if oi is not None else None]
        for stamp, o, h, l, c, v, oi in data
    ]


async def read_exact_crude_contract_from_store(database_url, trading_symbol, start, end):
    return await asyncio.to_thread(_read_exact_contract_sync, database_url, trading_symbol, start, end)
