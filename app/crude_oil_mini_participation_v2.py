from __future__ import annotations

from statistics import mean

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_market_perception import clean_ohlcv, latest_visible_index

DIRECTIONAL = {"BULLISH", "BEARISH"}
INITIATIVE_STATES = {"INITIATIVE_BUYING", "INITIATIVE_SELLING"}
DEFAULT_LOOKBACK_BARS = 6


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _direction(value: float | None) -> str:
    if value is None or value == 0:
        return "UNKNOWN"
    return "BULLISH" if value > 0 else "BEARISH"


def _empty(state: str, *, detail: dict | None = None) -> dict:
    return {
        "family": "PARTICIPATION",
        "causal_origin": "POSITIONING_FLOW",
        "independence_status": "NOT_DIRECTIONAL",
        "depends_on_origins": [],
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
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
) -> dict:
    """Describe commitment/acceptance from completed CRUDEOILM bars only.

    Price plus volume is deliberately not independent directional evidence. A vote is
    permitted only when fresh positioning evidence (OI) and auction acceptance align.
    This module is shadow/research-only and is not consumed by Current Mind.
    """
    if lookback_bars < 4:
        raise ValueError("lookback_bars must be at least 4")

    rows = clean_ohlcv(candles)
    index = latest_visible_index(rows, click_timestamp)
    if index is None:
        return _empty("NO_COMPLETED_BAR")

    visible = rows[: index + 1]
    if len(visible) < lookback_bars:
        return _empty("INSUFFICIENT_COMPLETED_BARS", detail={"visible_bars": len(visible)})

    sample = visible[-lookback_bars:]
    last = sample[-1]
    close = float(last[4])
    start_close = float(sample[0][4])
    move_direction = _direction(close - start_close)

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

    # The reference range must predate the two confirming closes. Including those
    # closes in the reference makes two-close acceptance impossible by construction.
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

    ranges = [max(0.0, float(row[2]) - float(row[3])) for row in sample]
    volumes = [max(0.0, float(row[5])) for row in sample]
    avg_range = mean(ranges[:-1]) if len(ranges) > 1 else 0.0
    avg_volume = mean(volumes[:-1]) if len(volumes) > 1 else 0.0
    range_expanded = avg_range > 0 and ranges[-1] > avg_range
    local_volume_expanded = avg_volume > 0 and volumes[-1] > avg_volume

    detail = {
        "click_timestamp": parse_ist_timestamp(click_timestamp).isoformat(),
        "lookback_bars": lookback_bars,
        "move_direction": move_direction,
        "time_adjusted_relative_volume": time_adjusted,
        "prior_confirming_level": confirming,
        "activity_expanded": activity_expanded,
        "latest_oi": latest_oi,
        "start_oi": start_oi,
        "oi_delta": oi_delta,
        "accepted_above_prior_range": accepted_above,
        "accepted_below_prior_range": accepted_below,
        "value_agrees": value_agrees,
        "range_expanded": range_expanded,
        "local_volume_expanded": local_volume_expanded,
    }

    if not activity_expanded:
        return _empty("LOW_OR_NORMAL_PARTICIPATION", detail=detail)
    if move_direction not in DIRECTIONAL:
        return _empty("ACTIVE_BALANCED", detail=detail)

    if oi_delta is None:
        return {
            "family": "PARTICIPATION",
            "causal_origin": "LOCAL_PRICE_VOLUME",
            "independence_status": "DEPENDENT_ON_LOCAL_PRICE",
            "depends_on_origins": ["LOCAL_PRICE_STRUCTURE"],
            "counts_for_direction": False,
            "stance": "UNKNOWN",
            "state": "PRICE_VOLUME_ONLY_DEPENDENT",
            "detail": detail,
        }

    if oi_delta < 0:
        return {
            "family": "PARTICIPATION",
            "causal_origin": "POSITIONING_FLOW",
            "independence_status": "INDEPENDENT_CONTEXT_ONLY",
            "depends_on_origins": [],
            "counts_for_direction": False,
            "stance": "UNKNOWN",
            "state": "SHORT_COVERING" if move_direction == "BULLISH" else "LONG_LIQUIDATION",
            "detail": detail,
        }

    accepted = accepted_above if move_direction == "BULLISH" else accepted_below
    if oi_delta > 0 and accepted and value_agrees and (range_expanded or local_volume_expanded):
        return {
            "family": "PARTICIPATION",
            "causal_origin": "POSITIONING_FLOW",
            "independence_status": "INDEPENDENT",
            "depends_on_origins": [],
            "counts_for_direction": True,
            "stance": move_direction,
            "state": "INITIATIVE_BUYING" if move_direction == "BULLISH" else "INITIATIVE_SELLING",
            "detail": detail,
        }

    return {
        "family": "PARTICIPATION",
        "causal_origin": "POSITIONING_FLOW",
        "independence_status": "INDEPENDENT_CONTEXT_ONLY",
        "depends_on_origins": [],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": (
            "SELLER_ABSORPTION" if oi_delta > 0 and move_direction == "BULLISH" and not accepted
            else "BUYER_ABSORPTION" if oi_delta > 0 and move_direction == "BEARISH" and not accepted
            else "ACTIVE_BALANCED"
        ),
        "detail": detail,
    }


def participation_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_PARTICIPATION_COMMITMENT_ACCEPTANCE_V2",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "default_lookback_bars": DEFAULT_LOOKBACK_BARS,
        "default_is_architecture_hypothesis_not_optimized": True,
        "directional_states": sorted(INITIATIVE_STATES),
        "price_volume_only_can_vote": False,
        "position_closure_states_can_vote": False,
        "requires_fresh_positioning_for_direction": True,
        "requires_acceptance_for_direction": True,
        "threshold_search_on_inspected_august_allowed": False,
        "promotion_allowed": False,
    }
