from __future__ import annotations

from datetime import datetime
from statistics import median

from .commodity_time import parse_ist_timestamp


def _timestamp(row: dict | None):
    if not row:
        return None
    raw = row.get("timestamp") or row.get("time") or row.get("datetime")
    return parse_ist_timestamp(raw) if raw else None


def _price(row: dict | None):
    if not row:
        return None
    for key in ("price", "close", "last_price"):
        try:
            return float(row[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _return(start: dict | None, end: dict | None):
    start_price = _price(start)
    end_price = _price(end)
    if start_price in (None, 0) or end_price is None:
        return None
    return (end_price - start_price) / start_price


def _market_index(candles: list[dict]):
    by_timestamp = {}
    session_dates = set()
    for candle in candles:
        ts = _timestamp(candle)
        price = _price(candle)
        if ts is None or price is None:
            continue
        by_timestamp[ts] = {"timestamp": ts, "price": price}
        session_dates.add(ts.date())
    return by_timestamp, sorted(session_dates)


def _same_clock_baseline(
    start: dict,
    end: dict,
    candles: list[dict],
    *,
    lookback_sessions: int,
    min_samples: int,
):
    start_ts = _timestamp(start)
    end_ts = _timestamp(end)
    actual_return = _return(start, end)
    base = {
        "status": "INSUFFICIENT_HISTORY",
        "actual_return": actual_return,
        "actual_abs_return": abs(actual_return) if actual_return is not None else None,
        "median_abs_return": None,
        "normalized_abs_move": None,
        "samples": 0,
        "sample_windows": [],
    }
    if start_ts is None or end_ts is None or actual_return is None:
        return {**base, "status": "INVALID_OBSERVATION"}
    if lookback_sessions <= 0 or min_samples <= 0:
        raise ValueError("lookback_sessions and min_samples must be positive")

    by_timestamp, session_dates = _market_index(candles)
    try:
        start_index = session_dates.index(start_ts.date())
        end_index = session_dates.index(end_ts.date())
    except ValueError:
        return base
    session_span = end_index - start_index
    if session_span < 0:
        return {**base, "status": "INVALID_OBSERVATION"}

    samples = []
    sample_windows = []
    for candidate_start_index in range(start_index - 1, -1, -1):
        candidate_end_index = candidate_start_index + session_span
        if candidate_end_index >= len(session_dates):
            continue
        candidate_start_ts = datetime.combine(
            session_dates[candidate_start_index], start_ts.timetz(), tzinfo=start_ts.tzinfo
        )
        candidate_end_ts = datetime.combine(
            session_dates[candidate_end_index], end_ts.timetz(), tzinfo=end_ts.tzinfo
        )
        # Historical context must have been fully observable before the segment
        # being normalized. This permits prior close->next-open analogues while
        # excluding any same/future observation from the event segment itself.
        if candidate_end_ts >= start_ts:
            continue
        start_row = by_timestamp.get(candidate_start_ts)
        end_row = by_timestamp.get(candidate_end_ts)
        if start_row is None or end_row is None:
            continue
        move = _return(start_row, end_row)
        if move is None:
            continue
        samples.append(abs(move))
        sample_windows.append(
            {
                "start": candidate_start_ts.isoformat(),
                "end": candidate_end_ts.isoformat(),
                "abs_return": abs(move),
            }
        )
        if len(samples) >= lookback_sessions:
            break

    if len(samples) < min_samples:
        return {**base, "samples": len(samples), "sample_windows": sample_windows}
    baseline = float(median(samples))
    ratio = abs(actual_return) / baseline if baseline > 0 else None
    return {
        **base,
        "status": "AVAILABLE",
        "median_abs_return": baseline,
        "normalized_abs_move": ratio,
        "samples": len(samples),
        "sample_windows": sample_windows,
    }


def assess_volatility_context(
    window: dict,
    candles: list[dict],
    *,
    lookback_sessions: int = 5,
    min_samples: int = 3,
    fixed_noise_floor: float = 0.0005,
):
    """Describe observed news moves relative to prior same-clock market motion.

    This is a shadow diagnostic only. It does not alter reaction state, observed
    path, participation, trade eligibility, or any P&L/outcome field.
    """
    segments = {
        "immediate": (window.get("pre_event"), window.get("immediate")),
        "confirmation": (window.get("pre_event"), window.get("confirmation")),
        "assimilation": (window.get("pre_event"), window.get("assimilation")),
        "follow": (window.get("immediate"), window.get("confirmation")),
    }
    results = {}
    for name, (start, end) in segments.items():
        result = _same_clock_baseline(
            start,
            end,
            candles,
            lookback_sessions=lookback_sessions,
            min_samples=min_samples,
        )
        baseline = result.get("median_abs_return")
        result["fixed_noise_floor"] = fixed_noise_floor
        result["baseline_to_fixed_floor"] = (
            baseline / fixed_noise_floor if baseline is not None and fixed_noise_floor > 0 else None
        )
        results[name] = result
    return {
        "mode": "VOLATILITY_CONTEXT_SHADOW_V1",
        "outcome_blind": True,
        "classification_unchanged": True,
        "lookback_sessions": lookback_sessions,
        "min_samples": min_samples,
        "segments": results,
        "rule": "Prior same-clock session moves provide shadow market-motion context only; they do not change reaction or trade decisions.",
    }
