from __future__ import annotations

from datetime import time
from typing import Any

from .commodity_time import parse_ist_timestamp

ACTION_DIRECTION = {"BUY_CE": "BULLISH", "BUY_PE": "BEARISH"}
PATTERN_LOOKBACK_BARS = 6
EMA_PERIOD = 20


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _ema(values: list[float], period: int = EMA_PERIOD) -> float | None:
    if not values:
        return None
    k = 2.0 / (period + 1.0)
    value = float(values[0])
    for raw in values[1:]:
        value = float(raw) * k + value * (1.0 - k)
    return value


def _session_indices(rows: list, index: int) -> list[int]:
    if index < 0 or index >= len(rows):
        return []
    day = parse_ist_timestamp(rows[index][0]).date()
    out = []
    for i in range(index + 1):
        try:
            if parse_ist_timestamp(rows[i][0]).date() == day:
                out.append(i)
        except (TypeError, ValueError, OverflowError):
            continue
    return out


def _opening_range(rows: list, index: int) -> dict:
    session = _session_indices(rows, index)
    if not session:
        return {"status": "UNAVAILABLE", "high": None, "low": None}
    current_ts = parse_ist_timestamp(rows[index][0])
    if current_ts.time() < time(10, 0):
        return {"status": "FORMING", "high": None, "low": None}
    opening = []
    for i in session:
        stamp = parse_ist_timestamp(rows[i][0])
        if time(9, 0) <= stamp.time() < time(10, 0):
            opening.append(rows[i])
    if not opening:
        return {"status": "UNAVAILABLE", "high": None, "low": None}
    return {
        "status": "READY",
        "high": max(float(row[2]) for row in opening),
        "low": min(float(row[3]) for row in opening),
    }


def _ema20_at(rows: list, index: int) -> float | None:
    if index < 0 or index >= len(rows):
        return None
    closes = [float(row[4]) for row in rows[: index + 1]]
    return _ema(closes, EMA_PERIOD)


def _recent_indices(index: int, *, include_current: bool = False) -> list[int]:
    end = index + 1 if include_current else index
    start = max(0, end - PATTERN_LOOKBACK_BARS)
    return list(range(start, end))


def _trend_pullback(rows: list, index: int, direction: str, structure: str) -> dict:
    expected_structure = "UPTREND" if direction == "BULLISH" else "DOWNTREND"
    if structure != expected_structure:
        return {"confirmed": False, "reason": "STRUCTURE_NOT_ALIGNED_WITH_TREND_PULLBACK"}
    if index < 1:
        return {"confirmed": False, "reason": "INSUFFICIENT_HISTORY"}

    current_close = float(rows[index][4])
    previous_close = float(rows[index - 1][4])
    current_ema = _ema20_at(rows, index)
    if current_ema is None:
        return {"confirmed": False, "reason": "EMA20_UNAVAILABLE"}

    prior = _recent_indices(index)
    touches = []
    for i in prior:
        ema = _ema20_at(rows, i)
        if ema is None:
            continue
        close = float(rows[i][4])
        if direction == "BULLISH" and close <= ema:
            touches.append(i)
        elif direction == "BEARISH" and close >= ema:
            touches.append(i)

    if direction == "BULLISH":
        reaccepted = current_close > current_ema and current_close > previous_close
    else:
        reaccepted = current_close < current_ema and current_close < previous_close

    return {
        "confirmed": bool(touches) and reaccepted,
        "reason": "PULLBACK_TO_EMA20_REACCEPTED" if bool(touches) and reaccepted else "NO_PULLBACK_REACCEPTANCE_SEQUENCE",
        "countertrend_ema_touch_bars": len(touches),
        "current_ema20": current_ema,
        "current_close": current_close,
        "previous_close": previous_close,
    }


def _breakout_retest(rows: list, index: int, direction: str) -> dict:
    opening = _opening_range(rows, index)
    if opening["status"] != "READY":
        return {"confirmed": False, "reason": f"OPENING_RANGE_{opening['status']}"}
    boundary = float(opening["high"] if direction == "BULLISH" else opening["low"])
    recent = _recent_indices(index)
    if not recent:
        return {"confirmed": False, "reason": "INSUFFICIENT_HISTORY"}

    breakout_positions = []
    for i in recent:
        close = float(rows[i][4])
        if direction == "BULLISH" and close > boundary:
            breakout_positions.append(i)
        elif direction == "BEARISH" and close < boundary:
            breakout_positions.append(i)
    if not breakout_positions:
        return {"confirmed": False, "reason": "NO_PRIOR_BREAKOUT_CLOSE", "boundary": boundary}

    first_break = breakout_positions[0]
    retest = False
    for i in range(first_break + 1, index + 1):
        high = float(rows[i][2])
        low = float(rows[i][3])
        close = float(rows[i][4])
        if direction == "BULLISH" and low <= boundary and close >= boundary:
            retest = True
        elif direction == "BEARISH" and high >= boundary and close <= boundary:
            retest = True
    current_close = float(rows[index][4])
    retained = current_close > boundary if direction == "BULLISH" else current_close < boundary
    return {
        "confirmed": retest and retained,
        "reason": "BREAKOUT_RETEST_RETAINED" if retest and retained else "NO_RETEST_AND_RETAIN_SEQUENCE",
        "boundary": boundary,
        "prior_breakout_close_bars": len(breakout_positions),
        "retest_seen": retest,
        "current_close_retains_breakout": retained,
    }


def _range_edge_reversal(rows: list, index: int, direction: str, structure: str) -> dict:
    if structure != "RANGE":
        return {"confirmed": False, "reason": "STRUCTURE_NOT_RANGE"}
    session = _session_indices(rows, index)
    prior = [i for i in session if i < index]
    if not prior:
        return {"confirmed": False, "reason": "NO_PRIOR_SESSION_EDGE"}
    prior_high = max(float(rows[i][2]) for i in prior)
    prior_low = min(float(rows[i][3]) for i in prior)
    current_open = float(rows[index][1])
    current_high = float(rows[index][2])
    current_low = float(rows[index][3])
    current_close = float(rows[index][4])

    if direction == "BULLISH":
        swept = current_low <= prior_low
        reclaimed = current_close > prior_low and current_close > current_open
        confirmed = swept and reclaimed
        reason = "LOW_EDGE_SWEEP_RECLAIM" if confirmed else "NO_LOW_EDGE_REJECTION"
        edge = prior_low
    else:
        swept = current_high >= prior_high
        reclaimed = current_close < prior_high and current_close < current_open
        confirmed = swept and reclaimed
        reason = "HIGH_EDGE_SWEEP_REJECTION" if confirmed else "NO_HIGH_EDGE_REJECTION"
        edge = prior_high
    return {
        "confirmed": confirmed,
        "reason": reason,
        "edge": edge,
        "edge_swept": swept,
        "closed_back_inside_with_reversal_body": reclaimed,
    }


def _failed_breakout(rows: list, index: int, direction: str) -> dict:
    opening = _opening_range(rows, index)
    if opening["status"] != "READY":
        return {"confirmed": False, "reason": f"OPENING_RANGE_{opening['status']}"}
    if index < 1:
        return {"confirmed": False, "reason": "INSUFFICIENT_HISTORY"}
    high = float(opening["high"])
    low = float(opening["low"])
    prior = _recent_indices(index)
    current_close = float(rows[index][4])

    if direction == "BULLISH":
        failed_side_bars = [i for i in prior if float(rows[i][4]) < low]
        reclaimed = current_close > low
        confirmed = bool(failed_side_bars) and reclaimed
        reason = "FAILED_BREAKDOWN_RECLAIMED" if confirmed else "NO_FAILED_BREAKDOWN_RECLAIM"
        failed_boundary = low
    else:
        failed_side_bars = [i for i in prior if float(rows[i][4]) > high]
        reclaimed = current_close < high
        confirmed = bool(failed_side_bars) and reclaimed
        reason = "FAILED_BREAKOUT_REJECTED" if confirmed else "NO_FAILED_BREAKOUT_REJECTION"
        failed_boundary = high
    return {
        "confirmed": confirmed,
        "reason": reason,
        "failed_boundary": failed_boundary,
        "prior_outside_close_bars": len(failed_side_bars),
        "current_close_back_through_boundary": reclaimed,
    }


def assess_declared_playbook_pattern(rows: list, index: int, journal: dict) -> dict:
    """Verify a declared playbook using only frozen OHLC information visible at the click.

    V1 deliberately uses literal chart-sequence semantics rather than outcome-fitted scores. The
    six-bar sequence window reuses Current Mind's already-frozen structural invalidation window;
    it was not selected from August trade results.
    """
    decision = _dict(journal.get("decision"))
    action = str(decision.get("action") or "NO_TRADE")
    direction = ACTION_DIRECTION.get(action)
    playbook = str(decision.get("playbook") or "")
    structure = str(
        _dict(_dict(journal.get("regime")).get("observations")).get("trend_structure")
        or "UNKNOWN"
    ).upper()

    base = {
        "mode": "PLAYBOOK_PATTERN_CONFIRMATION_SHADOW_V1",
        "outcome_blind": True,
        "shadow_only": True,
        "changes_decision": False,
        "pattern_lookback_bars": PATTERN_LOOKBACK_BARS,
        "pattern_lookback_source": "FROZEN_CURRENT_MIND_INVALIDATION_WINDOW",
        "declared_playbook": playbook or None,
        "baseline_action": action,
        "direction": direction or "UNKNOWN",
        "trend_structure": structure,
    }
    if not playbook:
        return {**base, "status": "NO_DECLARED_PLAYBOOK", "confirmed": False, "detail": {}}
    if direction not in {"BULLISH", "BEARISH"}:
        return {**base, "status": "NON_DIRECTIONAL_BASELINE", "confirmed": False, "detail": {}}
    if index < 0 or index >= len(rows):
        return {**base, "status": "CLICK_ROW_UNAVAILABLE", "confirmed": False, "detail": {}}

    if playbook == "TREND_PULLBACK":
        detail = _trend_pullback(rows, index, direction, structure)
    elif playbook == "BREAKOUT_RETEST":
        detail = _breakout_retest(rows, index, direction)
    elif playbook == "RANGE_EDGE_REVERSAL":
        detail = _range_edge_reversal(rows, index, direction, structure)
    elif playbook == "FAILED_BREAKOUT":
        detail = _failed_breakout(rows, index, direction)
    else:
        return {**base, "status": "UNKNOWN_DECLARED_PLAYBOOK", "confirmed": False, "detail": {}}

    confirmed = bool(detail.get("confirmed"))
    return {
        **base,
        "status": "PATTERN_CONFIRMED" if confirmed else "PATTERN_NOT_CONFIRMED",
        "confirmed": confirmed,
        "detail": detail,
        "rule": (
            "A named playbook is confirmed only when a literal pre-click chart sequence matching "
            "its semantics is present. Regime eligibility, evidence coherence, generic recent-high/low "
            "trade geometry, catalyst direction, and later trade outcome are not substitutes."
        ),
    }
