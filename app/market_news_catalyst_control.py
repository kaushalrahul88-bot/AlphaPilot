from __future__ import annotations

from datetime import timedelta
from typing import Any

from .commodity_time import parse_ist_timestamp

DIRECTION_MAP = {"UP": "BULLISH", "DOWN": "BEARISH"}
OPPOSITE_STRUCTURE = {"BULLISH": "DOWNTREND", "BEARISH": "UPTREND"}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_timestamp(row: Any):
    if isinstance(row, dict):
        raw = row.get("timestamp") or row.get("time") or row.get("datetime")
    elif isinstance(row, (list, tuple)) and row:
        raw = row[0]
    else:
        raw = None
    if raw is None:
        return None
    try:
        return parse_ist_timestamp(raw)
    except (TypeError, ValueError):
        return None


def _row_close(row: Any) -> float | None:
    if isinstance(row, dict):
        for key in ("close", "price", "last_price"):
            value = _number(row.get(key))
            if value is not None:
                return value
        return None
    if isinstance(row, (list, tuple)) and len(row) >= 5:
        return _number(row[4])
    return None


def _unavailable(state: str, reason: str, **extra) -> dict:
    return {
        "mode": "MARKET_NEWS_CATALYST_CONTROL_SHADOW_V1",
        "outcome_blind": True,
        "shadow_only": True,
        "state": state,
        "direction": "UNKNOWN",
        "controls_direction": False,
        "reason": reason,
        **extra,
    }


def assess_catalyst_control(
    reaction_record: dict,
    candles: list,
    *,
    click_timestamp: str,
    market_structure: str | None = None,
    max_horizon_hours: float = 8.0,
) -> dict:
    """Describe whether a completed news reaction still controls the market at a click.

    This is an outcome-blind shadow classifier. Direction comes only from the existing
    materiality-qualified assimilation path. The pre-event close is the catalyst origin
    and the +60m assimilation close is the accepted-reaction reference. A close through
    the origin in the opposite direction permanently marks that catalyst as overridden
    for the inspected path. No trade outcome, P&L, headline stance, or option action is
    read, and the returned state never changes production behavior.
    """
    if max_horizon_hours <= 0:
        raise ValueError("max_horizon_hours must be positive")
    record = _dict(reaction_record)
    if record.get("coverage_status") != "CLASSIFIABLE":
        return _unavailable("UNOBSERVED", "REACTION_NOT_CLASSIFIABLE")

    event = _dict(record.get("event"))
    if str(event.get("disposition") or "").upper() == "BLOCK":
        return _unavailable("UNOBSERVED", "EVENT_BLOCKED")

    window = _dict(record.get("window"))
    qualified = _dict(record.get("materiality_qualified_path"))
    if qualified.get("observation_status") != "OBSERVED":
        return _unavailable("UNOBSERVED", "QUALIFIED_PATH_NOT_OBSERVED")

    raw_direction = str(_dict(qualified.get("qualified_directions")).get("assimilation") or "").upper()
    direction = DIRECTION_MAP.get(raw_direction)
    if direction is None:
        return _unavailable("UNOBSERVED", "ASSIMILATION_DIRECTION_NOT_QUALIFIED")

    pre = _dict(window.get("pre_event"))
    assimilation = _dict(window.get("assimilation"))
    origin_price = _number(pre.get("price"))
    accepted_price = _number(assimilation.get("price"))
    assimilation_raw = assimilation.get("timestamp")
    anchor_raw = window.get("reaction_anchor_timestamp")
    if origin_price is None or accepted_price is None or not assimilation_raw or not anchor_raw:
        return _unavailable(
            "UNOBSERVED",
            "REACTION_REFERENCE_LEVELS_MISSING",
            direction=direction,
        )

    try:
        click = parse_ist_timestamp(click_timestamp)
        assimilation_ts = parse_ist_timestamp(assimilation_raw)
        anchor_ts = parse_ist_timestamp(anchor_raw)
    except (TypeError, ValueError):
        return _unavailable("UNOBSERVED", "INVALID_TIMESTAMP", direction=direction)

    base = {
        "mode": "MARKET_NEWS_CATALYST_CONTROL_SHADOW_V1",
        "outcome_blind": True,
        "shadow_only": True,
        "direction": direction,
        "reaction_anchor_timestamp": anchor_ts.isoformat(),
        "assimilation_observed_at": assimilation_ts.isoformat(),
        "click_timestamp": click.isoformat(),
        "origin_price": origin_price,
        "accepted_reaction_price": accepted_price,
        "market_structure": str(market_structure or "UNKNOWN").upper(),
        "max_horizon_hours": float(max_horizon_hours),
        "reference_rule": "PRE_EVENT_CLOSE_ORIGIN_AND_PLUS_60M_ASSIMILATION_CLOSE_ACCEPTANCE",
        "override_rule": "FIRST_POST_ASSIMILATION_CLOSE_THROUGH_ORIGIN_OPPOSITE_REACTION_DIRECTION",
    }
    if click < assimilation_ts:
        return {
            **base,
            "state": "NOT_YET_ASSIMILATED",
            "controls_direction": False,
            "reason": "CLICK_PRECEDES_COMPLETED_ASSIMILATION",
            "current_price": None,
            "override_seen_at": None,
        }
    if click > anchor_ts + timedelta(hours=max_horizon_hours):
        return {
            **base,
            "state": "OUTSIDE_OBSERVATION_HORIZON",
            "controls_direction": False,
            "reason": "CLICK_AFTER_FROZEN_MAXIMUM_CONTEXT_HORIZON",
            "current_price": None,
            "override_seen_at": None,
        }

    visible = []
    for candle in candles or []:
        ts = _row_timestamp(candle)
        close = _row_close(candle)
        if ts is None or close is None or ts < assimilation_ts or ts > click:
            continue
        visible.append((ts, close))
    visible.sort(key=lambda item: item[0])
    if not visible:
        return {
            **base,
            "state": "UNOBSERVED",
            "controls_direction": False,
            "reason": "NO_POST_ASSIMILATION_CANDLE_VISIBLE_BY_CLICK",
            "current_price": None,
            "override_seen_at": None,
        }

    current_ts, current_price = visible[-1]
    if direction == "BULLISH":
        override = next(((ts, close) for ts, close in visible if close < origin_price), None)
        at_or_beyond_acceptance = current_price >= accepted_price
        on_reaction_side_of_origin = current_price >= origin_price
    else:
        override = next(((ts, close) for ts, close in visible if close > origin_price), None)
        at_or_beyond_acceptance = current_price <= accepted_price
        on_reaction_side_of_origin = current_price <= origin_price

    opposite_structure = base["market_structure"] == OPPOSITE_STRUCTURE[direction]
    if override is not None:
        state = "CONTROL_OVERRIDDEN"
        controls = False
        reason = "PRICE_CLOSED_THROUGH_CATALYST_ORIGIN"
    elif opposite_structure:
        state = "CONTROL_CONTESTED"
        controls = False
        reason = "CLICK_TIME_STRUCTURE_OPPOSES_REACTION_BEFORE_ORIGIN_BREAK"
    elif at_or_beyond_acceptance:
        state = "CONTROL_ACTIVE"
        controls = True
        reason = "PRICE_RETAINS_ACCEPTED_REACTION_LEVEL"
    elif on_reaction_side_of_origin:
        state = "CONTROL_ASSIMILATING"
        controls = False
        reason = "PRICE_RETAINS_REACTION_SIDE_OF_ORIGIN_BUT_NOT_ACCEPTED_LEVEL"
    else:
        state = "CONTROL_OVERRIDDEN"
        controls = False
        reason = "CURRENT_PRICE_OPPOSITE_CATALYST_ORIGIN"

    return {
        **base,
        "state": state,
        "controls_direction": controls,
        "reason": reason,
        "current_price": current_price,
        "current_price_timestamp": current_ts.isoformat(),
        "post_assimilation_observations": len(visible),
        "override_seen_at": override[0].isoformat() if override else None,
        "override_close": override[1] if override else None,
        "at_or_beyond_accepted_reaction_price": at_or_beyond_acceptance,
        "on_reaction_side_of_origin": on_reaction_side_of_origin,
        "opposite_market_structure": opposite_structure,
        "rule": (
            "A qualified catalyst controls only while price still retains the accepted +60m reaction "
            "level and click-time structure does not oppose it. Retreat inside the origin/acceptance "
            "range is assimilation, opposite structure is contested, and any close through the "
            "pre-event origin permanently marks the inspected catalyst path overridden."
        ),
    }


def catalyst_control_context(
    reaction_records: list[dict],
    candles: list,
    *,
    click_timestamp: str,
    market_structure: str | None = None,
    max_horizon_hours: float = 8.0,
) -> dict:
    """Aggregate per-reaction control states without creating a trading signal."""
    assessments = [
        assess_catalyst_control(
            record,
            candles,
            click_timestamp=click_timestamp,
            market_structure=market_structure,
            max_horizon_hours=max_horizon_hours,
        )
        for record in reaction_records or []
    ]
    eligible = [
        row
        for row in assessments
        if row.get("state")
        in {"CONTROL_ACTIVE", "CONTROL_ASSIMILATING", "CONTROL_CONTESTED", "CONTROL_OVERRIDDEN"}
    ]
    if not eligible:
        return {
            "mode": "MARKET_NEWS_CATALYST_CONTROL_CONTEXT_SHADOW_V1",
            "outcome_blind": True,
            "shadow_only": True,
            "state": "NO_CATALYST_CONTROL_CONTEXT",
            "direction": "UNKNOWN",
            "controls_direction": False,
            "primary": None,
            "controls": [],
            "aggregation_rule": "LATEST_COMPLETED_QUALIFIED_CATALYST_WITHIN_FROZEN_HORIZON",
        }

    directions = {row.get("direction") for row in eligible if row.get("direction") in {"BULLISH", "BEARISH"}}
    if len(directions) > 1:
        return {
            "mode": "MARKET_NEWS_CATALYST_CONTROL_CONTEXT_SHADOW_V1",
            "outcome_blind": True,
            "shadow_only": True,
            "state": "CONFLICTING_CATALYST_DIRECTIONS",
            "direction": "UNKNOWN",
            "controls_direction": False,
            "primary": None,
            "controls": eligible,
            "aggregation_rule": "CONFLICTING_DIRECTIONS_FAIL_CLOSED_TO_NON_DIRECTIONAL_SHADOW",
        }

    primary = max(eligible, key=lambda row: parse_ist_timestamp(row["assimilation_observed_at"]))
    return {
        "mode": "MARKET_NEWS_CATALYST_CONTROL_CONTEXT_SHADOW_V1",
        "outcome_blind": True,
        "shadow_only": True,
        "state": primary["state"],
        "direction": primary["direction"],
        "controls_direction": bool(primary["controls_direction"]),
        "primary": primary,
        "controls": eligible,
        "aggregation_rule": "LATEST_COMPLETED_QUALIFIED_CATALYST_WITHIN_FROZEN_HORIZON",
    }
