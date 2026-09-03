from __future__ import annotations

from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_market_perception import clean_ohlcv, latest_visible_index

IST = ZoneInfo("Asia/Kolkata")
DIRECTIONAL = {"BULLISH", "BEARISH"}
INITIATIVE_STATES = {"INITIATIVE_BUYING", "INITIATIVE_SELLING"}


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ts(value) -> datetime:
    return parse_ist_timestamp(value).astimezone(IST)


def _direction(value: float | None) -> str:
    if value is None or value == 0:
        return "UNKNOWN"
    return "BULLISH" if value > 0 else "BEARISH"


def _empty(state: str, *, detail: dict | None = None) -> dict:
    return {
        "family": "PARTICIPATION",
        "causal_origin": "POSITIONING_FLOW",
        "independence_status": "NOT_DIRECTIONAL",
        "depends_on": [],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": state,
        "detail": detail or {},
    }


def build_participation_observation(
    candles,
    *,
    click_timestamp: str,
    snapshot: dict,
    profile: dict,
    lookback_bars: int = 6,
) -> dict:
    """Build a shadow-only commitment/acceptance observation from completed Mini bars.

    Price plus volume alone is deliberately not an independent directional vote. A
    directional participation state requires fresh positioning evidence (OI), auction
    acceptance, and directional progress. This object is research-only and is not
    consumed by Current Mind.
    """
    rows = clean_ohlcv(candles)
    index = latest_visible_index(rows, click_timestamp)
    if index is None:
        return _empty("NO_COMPLETED_BAR")

    visible = rows[: index + 1]
    if len(visible) < max(4, lookback_bars):
        return _empty("INSUFFICIENT_COMPLETED_BARS", detail={"visible_bars": len(visible)})

    sample = visible[-lookback_bars:]
    last = sample[-1]
    close = float(last[4])
    start_close = float(sample[0][4])
    move = close - start_close
    move_direction = _direction(move)

    time_adjusted = _f(snapshot.get("time_adjusted_relative_volume"))
    confirming = _f((profile or {}).get("participation_confirming"))
    activity_expanded = (
        time_adjusted is not None
        and confirming is not None
        and time_adjusted >= confirming
    )

    latest_oi = _f(last[6]) if len(last) > 6 else None
    start_oi = _f(sample[0][6]) if len(sample[0]) > 6 else None
    oi_delta = (latest_oi - start_oi) if latest_oi is not None and start_oi is not None else None
    oi_direction = _direction(oi_delta)

    # The reference predates both confirming closes. Including either confirming bar
    # in the range would make a two-close acceptance test self-referential.
    reference = sample[:-2]
    confirming_bars = sample[-2:]
    prior_high = max(float(row[2]) for row in reference)
    prior_low = min(float(row[3]) for row in reference)
    confirming_closes = [float(row[4]) for row in confirming_bars]
    accepted_above = all(value > prior_high for value in confirming_closes)
    accepted_below = all(value < prior_low for value in confirming_closes)

    vwap_gap = _f(snapshot.get("session_vwap_gap_pct"))
    value_agrees = (
        (move_direction == "BULLISH" and vwap_gap is not None and vwap_gap > 0)
        or (move_direction == "BEARISH" and vwap_gap is not None and vwap_gap < 0)
    )

    recent_ranges = [max(0.0, float(row[2]) - float(row[3])) for row in sample]
    recent_volumes = [max(0.0, float(row[5])) for row in sample]
    avg_range = mean(recent_ranges[:-1]) if len(recent_ranges) > 1 else 0.0
    avg_volume = mean(recent_volumes[:-1]) if len(recent_volumes) > 1 else 0.0
    last_range = recent_ranges[-1]
    last_volume = recent_volumes[-1]
    range_expanded = avg_range > 0 and last_range > avg_range
    local_volume_expanded = avg_volume > 0 and last_volume > avg_volume

    base_detail = {
        "click_timestamp": _ts(click_timestamp).isoformat(),
        "lookback_bars": lookback_bars,
        "move_direction": move_direction,
        "time_adjusted_relative_volume": time_adjusted,
        "prior_confirming_level": confirming,
        "activity_expanded": activity_expanded,
        "latest_oi": latest_oi,
        "start_oi": start_oi,
        "oi_delta": oi_delta,
        "oi_direction": oi_direction,
        "acceptance_reference_high": prior_high,
        "acceptance_reference_low": prior_low,
        "accepted_above_prior_range": accepted_above,
        "accepted_below_prior_range": accepted_below,
        "value_agrees": value_agrees,
        "range_expanded": range_expanded,
        "local_volume_expanded": local_volume_expanded,
    }

    if not activity_expanded:
        return _empty("LOW_OR_NORMAL_PARTICIPATION", detail=base_detail)

    if move_direction not in DIRECTIONAL:
        return _empty("ACTIVE_BALANCED", detail=base_detail)

    if oi_delta is None:
        return {
            "family": "PARTICIPATION",
            "causal_origin": "LOCAL_PRICE_VOLUME",
            "independence_status": "DEPENDENT_ON_LOCAL_PRICE",
            "depends_on": ["LOCAL_PRICE_STRUCTURE"],
            "counts_for_direction": False,
            "stance": "UNKNOWN",
            "state": "PRICE_VOLUME_ONLY_DEPENDENT",
            "detail": base_detail,
        }

    if oi_delta < 0:
        state = "SHORT_COVERING" if move_direction == "BULLISH" else "LONG_LIQUIDATION"
        return {
            "family": "PARTICIPATION",
            "causal_origin": "POSITIONING_FLOW",
            "independence_status": "INDEPENDENT_CONTEXT_ONLY",
            "depends_on": [],
            "counts_for_direction": False,
            "stance": "UNKNOWN",
            "state": state,
            "detail": base_detail,
        }

    accepted = accepted_above if move_direction == "BULLISH" else accepted_below
    if oi_delta > 0 and accepted and value_agrees and (range_expanded or local_volume_expanded):
        state = "INITIATIVE_BUYING" if move_direction == "BULLISH" else "INITIATIVE_SELLING"
        return {
            "family": "PARTICIPATION",
            "causal_origin": "POSITIONING_FLOW",
            "independence_status": "INDEPENDENT",
            "depends_on": [],
            "counts_for_direction": True,
            "stance": move_direction,
            "state": state,
            "detail": base_detail,
        }

    if oi_delta > 0 and not accepted:
        state = "SELLER_ABSORPTION" if move_direction == "BULLISH" else "BUYER_ABSORPTION"
    else:
        state = "ACTIVE_BALANCED"
    return {
        "family": "PARTICIPATION",
        "causal_origin": "POSITIONING_FLOW",
        "independence_status": "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": state,
        "detail": base_detail,
    }


def participation_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_PARTICIPATION_COMMITMENT_ACCEPTANCE_V2",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "directional_states": sorted(INITIATIVE_STATES),
        "price_volume_only_can_vote": False,
        "position_closure_states_can_vote": False,
        "requires_fresh_positioning_for_direction": True,
        "requires_acceptance_for_direction": True,
        "default_lookback_is_design_hypothesis_not_validated_optimum": True,
        "threshold_search_on_inspected_august_allowed": False,
        "promotion_allowed": False,
    }
