from __future__ import annotations

import math
from statistics import mean


BOOK_KNOWLEDGE_REVISION = "SUNIL_GURJAR_PRICE_ACTION_PUBLIC_CONCEPTS_V1"


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _true_range(rows: list[list], index: int) -> float:
    row = rows[index]
    high, low = _number(row[2]), _number(row[3])
    if index <= 0:
        return max(0.0, high - low)
    previous_close = _number(rows[index - 1][4])
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def _local_atr(rows: list[list], index: int, period: int = 14) -> float:
    if not rows or index < 0:
        return 0.0
    start = max(0, index - period + 1)
    values = [_true_range(rows, item) for item in range(start, index + 1)]
    return mean(values) if values else 0.0


def _market_structure(rows: list[list], index: int) -> str:
    """Coarse, setup-time swing structure; never reads a future candle."""
    start = max(0, index - 20)
    window = rows[start:index]
    if len(window) < 10:
        return "UNKNOWN"
    split = len(window) // 2
    earlier, recent = window[:split], window[split:]
    earlier_high = max(_number(row[2]) for row in earlier)
    earlier_low = min(_number(row[3]) for row in earlier)
    recent_high = max(_number(row[2]) for row in recent)
    recent_low = min(_number(row[3]) for row in recent)
    if recent_high > earlier_high and recent_low > earlier_low:
        return "UPTREND"
    if recent_high < earlier_high and recent_low < earlier_low:
        return "DOWNTREND"
    return "RANGE_OR_TRANSITION"


def _candlestick_pattern(rows: list[list], index: int) -> str:
    row = rows[index]
    open_price, high, low, close = map(_number, row[1:5])
    candle_range = max(high - low, 1e-12)
    body = abs(close - open_price)
    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    if body / candle_range <= 0.15:
        return "DOJI"
    if index > 0:
        previous = rows[index - 1]
        previous_open, previous_close = _number(previous[1]), _number(previous[4])
        if close > open_price and previous_close < previous_open and open_price <= previous_close and close >= previous_open:
            return "BULLISH_ENGULFING"
        if close < open_price and previous_close > previous_open and open_price >= previous_close and close <= previous_open:
            return "BEARISH_ENGULFING"
    if lower_wick >= max(body * 2.0, candle_range * 0.35) and close >= low + candle_range * 0.60:
        return "BULLISH_REJECTION"
    if upper_wick >= max(body * 2.0, candle_range * 0.35) and close <= low + candle_range * 0.40:
        return "BEARISH_REJECTION"
    if body / candle_range >= 0.70:
        return "BULLISH_WIDE_BODY" if close > open_price else "BEARISH_WIDE_BODY"
    return "UNCLASSIFIED"


def price_action_snapshot(
    rows: list[list],
    index: int,
    direction: str,
    atr: float | None = None,
    breakout_level: float | None = None,
) -> dict:
    """Translate book themes into explicit, testable setup-time measurements.

    The author/source attribution applies to the concepts (structure, support and
    resistance, candle context, breakouts, volume and fakeouts). Thresholds and
    the quality score are AlphaPilot research hypotheses, not quoted book rules.
    """
    if index < 0 or index >= len(rows):
        raise ValueError("price-action snapshot index is outside candle history")
    normalized_direction = str(direction).upper()
    if normalized_direction not in {"LONG", "SHORT"}:
        raise ValueError("price-action direction must be LONG or SHORT")

    row = rows[index]
    open_price, high, low, close = map(_number, row[1:5])
    candle_range = max(high - low, 1e-12)
    body_ratio = abs(close - open_price) / candle_range
    close_location = (close - low) / candle_range
    upper_wick_ratio = (high - max(open_price, close)) / candle_range
    lower_wick_ratio = (min(open_price, close) - low) / candle_range
    against_wick_ratio = upper_wick_ratio if normalized_direction == "LONG" else lower_wick_ratio

    atr_value = _number(atr) if atr is not None else _local_atr(rows, index)
    atr_value = max(atr_value, 1e-12)
    prior = rows[max(0, index - 20):index]
    prior_high = max((_number(item[2]) for item in prior), default=high)
    prior_low = min((_number(item[3]) for item in prior), default=low)
    recent = prior[-8:]
    recent_range = (
        max(_number(item[2]) for item in recent) - min(_number(item[3]) for item in recent)
        if recent else 0.0
    )
    prior_range = max(prior_high - prior_low, 1e-12)
    compression_ratio = recent_range / prior_range

    prior_volumes = [_number(item[5]) for item in prior if len(item) > 5 and _number(item[5]) > 0]
    average_volume = mean(prior_volumes) if prior_volumes else 0.0
    current_volume = _number(row[5]) if len(row) > 5 else 0.0
    volume_ratio = current_volume / average_volume if average_volume > 0 else 1.0

    structure = _market_structure(rows, index)
    structure_aligned = (
        structure == "UPTREND" if normalized_direction == "LONG"
        else structure == "DOWNTREND"
    )
    resolved_level = _number(breakout_level, float("nan"))
    has_level = math.isfinite(resolved_level)
    breakout_distance_atr = (
        (close - resolved_level) / atr_value if normalized_direction == "LONG"
        else (resolved_level - close) / atr_value
    ) if has_level else 0.0
    breakout_close_confirmed = has_level and breakout_distance_atr > 0.0
    directional_close = close_location >= 0.70 if normalized_direction == "LONG" else close_location <= 0.30

    evidence = {
        "structure_aligned": structure_aligned,
        "level_close_confirmed": breakout_close_confirmed,
        "directional_close": directional_close,
        "body_at_least_0_55": body_ratio >= 0.55,
        "volume_at_least_1_20x": volume_ratio >= 1.20,
        "small_rejection_wick": against_wick_ratio <= 0.25,
        "pre_break_compression": compression_ratio <= 0.70,
    }
    quality_score = sum(bool(value) for value in evidence.values())
    if (
        breakout_close_confirmed
        and body_ratio >= 0.55
        and directional_close
        and volume_ratio >= 1.20
        and against_wick_ratio <= 0.25
    ):
        false_breakout_risk = "LOW"
    elif breakout_close_confirmed and body_ratio >= 0.40 and volume_ratio >= 1.0:
        false_breakout_risk = "MEDIUM"
    else:
        false_breakout_risk = "HIGH"
    if quality_score >= 5 and false_breakout_risk == "LOW":
        grade = "CONFIRMED"
    elif quality_score >= 3 and false_breakout_risk != "HIGH":
        grade = "ACCEPTABLE"
    else:
        grade = "WEAK"

    return {
        "knowledge_revision": BOOK_KNOWLEDGE_REVISION,
        "market_structure": structure,
        "structure_aligned": structure_aligned,
        "prior_support": round(prior_low, 4),
        "prior_resistance": round(prior_high, 4),
        "breakout_level": round(resolved_level, 4) if has_level else None,
        "breakout_distance_atr": round(breakout_distance_atr, 4),
        "breakout_close_confirmed": breakout_close_confirmed,
        "body_ratio": round(body_ratio, 4),
        "close_location": round(close_location, 4),
        "against_direction_wick_ratio": round(against_wick_ratio, 4),
        "volume_ratio": round(volume_ratio, 4),
        "compression_ratio": round(compression_ratio, 4),
        "candlestick_pattern": _candlestick_pattern(rows, index),
        "evidence": evidence,
        "false_breakout_risk": false_breakout_risk,
        "quality_score": quality_score,
        "price_action_grade": grade,
    }


def price_action_breakout_signal(
    rows: list[list],
    indices: list[int],
    atrs: list[float],
):
    """AlphaPilot-defined breakout hypothesis informed by the book's concepts."""
    first = indices[0] if indices else 0
    for index in indices:
        timestamp = str(rows[index][0])
        slot = timestamp[11:16]
        if slot < "09:45" or slot > "14:00" or index - first < 20:
            continue
        atr = _number(atrs[index]) if index < len(atrs) else _local_atr(rows, index)
        if atr <= 0:
            continue
        prior = rows[max(first, index - 20):index]
        resistance = max(_number(row[2]) for row in prior)
        support = min(_number(row[3]) for row in prior)
        close = _number(rows[index][4])
        candidates = []
        if close >= resistance + 0.10 * atr:
            candidates.append(("LONG", resistance))
        if close <= support - 0.10 * atr:
            candidates.append(("SHORT", support))
        for direction, level in candidates:
            snapshot = price_action_snapshot(rows, index, direction, atr, level)
            if (
                snapshot["breakout_distance_atr"] < 0.10
                or snapshot["body_ratio"] < 0.55
                or snapshot["false_breakout_risk"] != "LOW"
                or snapshot["quality_score"] < 4
            ):
                continue
            if direction == "LONG":
                stop = max(level - 0.25 * atr, close - 1.25 * atr)
            else:
                stop = min(level + 0.25 * atr, close + 1.25 * atr)
            return index, direction, stop, {
                "breakout_level": round(level, 4),
                "book_price_action": snapshot,
            }
    return None
