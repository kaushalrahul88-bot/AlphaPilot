from __future__ import annotations

from copy import deepcopy

from .crude_oil_mini_direction_brain_v2_integrated import _thesis
from .current_mind_thesis_builder import build_current_mind_decision


FIRST_LIVE_CLICK_AT = "2026-09-03T23:02:01.916091+05:30"
FIRST_LIVE_REFERENCE_CONTRACT = "CRUDEOILM21SEP26FUT"

# Frozen from the original live artifact. These values are comparison inputs only;
# later market outcomes are never fed back into the decision path.
FIRST_LIVE_BASELINE = {
    "mode": "CRUDE_OIL_MINI_CURRENT_MIND_LIVE_SHADOW_V1",
    "click_at": FIRST_LIVE_CLICK_AT,
    "reference_contract": FIRST_LIVE_REFERENCE_CONTRACT,
    "latest_completed_bar_available_at": "2026-09-03T23:00:00+05:30",
    "current_mind": {
        "action": "NO_TRADE",
        "direction": None,
        "reason": "EVIDENCE_NOT_COHERENT",
        "evidence_quality": "CONFLICTED",
        "playbook": None,
    },
    "integrated_v2_shadow": {
        "direction": "UNKNOWN",
        "confidence": "WEAK",
        "thesis_state": "INSUFFICIENT_INDEPENDENT_CONFIRMATION",
        "supporting_families": ["GLOBAL_CRUDE"],
        "opposing_families": [],
    },
    "data": {
        "candles": 10529,
        "historical_direction_memory_cases": 2208,
        "option_positioning": "NOT_WIRED_IN_LIVE_SHADOW_V1",
        "news": "NOT_WIRED_IN_LIVE_SHADOW_V1",
        "futures_oi": "USE_IF_PRESENT_OTHERWISE_NOT_RECONSTRUCTED",
    },
}

# Exact decision-driving state preserved by the original 23:02 artifact. Reusing
# this state is stricter than refetching historical candles a day later: it avoids
# provider-history drift while allowing today's decision code to be exercised.
_FROZEN_REGIME = {
    "regime_labels": ["TRENDING", "OPENING_EXPANSION", "PARTICIPATION_FADING"],
}
_FROZEN_EVIDENCE = {
    "independent_bullish_lanes": ["EXPERIENCE", "STRUCTURE"],
    "independent_bearish_lanes": ["STRUCTURE"],
    "contradictory_lanes": ["STRUCTURE"],
}
_FROZEN_V2_FAMILIES = {
    "LOCAL_STRUCTURE": {
        "family": "LOCAL_STRUCTURE",
        "causal_origin": "LOCAL_PRICE_STRUCTURE",
        "independence_status": "INDEPENDENT_CONTEXT_ONLY",
        "depends_on_origins": [],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": "INTERNAL_CONTRADICTION",
        "detail": {
            "structure": "UPTREND",
            "return_15m_pct": -0.287786347415675,
            "return_60m_pct": 0.046200046200040035,
            "momentum_15m": "BEARISH",
            "momentum_60m": "BULLISH",
        },
    },
    "PARTICIPATION": {
        "family": "PARTICIPATION",
        "causal_origin": "POSITIONING_FLOW",
        "independence_status": "NOT_DIRECTIONAL",
        "depends_on_origins": [],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": "LOW_OR_NORMAL_PARTICIPATION",
        "detail": {
            "activity_expanded": False,
            "time_adjusted_relative_volume": 0.006971038656605397,
            "prior_confirming_level": 2.1734196774614256,
            "move_direction": "BULLISH",
            "accepted_above_prior_range": False,
            "accepted_below_prior_range": False,
            "value_agrees": True,
            "range_expanded": False,
            "local_volume_expanded": False,
            "futures_oi_required": False,
            "option_positioning_primary": True,
        },
    },
    "GLOBAL_CRUDE": {
        "family": "GLOBAL_CRUDE",
        "causal_origin": "CROSS_MARKET_CRUDE",
        "independence_status": "INDEPENDENT",
        "depends_on_origins": [],
        "counts_for_direction": True,
        "stance": "BEARISH",
        "state": "WTI_BRENT_STRUCTURE_MOMENTUM_CONFIRMED",
    },
    "EVENT_REACTION": {
        "family": "EVENT_REACTION",
        "causal_origin": "EXOGENOUS_INFORMATION",
        "independence_status": "INDEPENDENT_CONTEXT_ONLY",
        "depends_on_origins": [],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": "NO_VISIBLE_EVENT",
    },
    "DIRECTION_MEMORY": {
        "family": "DIRECTION_MEMORY",
        "causal_origin": "HISTORICAL_ANALOGUE",
        "independence_status": "INDEPENDENT_CONTEXT_ONLY",
        "depends_on_origins": [],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": "READY",
    },
}


def _decision_view(result: dict) -> dict:
    current = result.get("current_mind") or {}
    v2 = result.get("integrated_v2_shadow") or {}
    data = result.get("data") or {}
    option_positioning = data.get("option_positioning") or {}
    news = data.get("news") or {}
    return {
        "mode": result.get("mode"),
        "click_at": result.get("click_at"),
        "reference_contract": result.get("reference_contract"),
        "latest_completed_bar_available_at": result.get("latest_completed_bar_available_at"),
        "current_mind": {
            key: current.get(key)
            for key in ("action", "direction", "reason", "evidence_quality", "playbook")
        },
        "integrated_v2_shadow": {
            key: v2.get(key)
            for key in (
                "direction",
                "confidence",
                "thesis_state",
                "supporting_families",
                "opposing_families",
            )
        },
        "data": {
            "candles": data.get("candles"),
            "historical_direction_memory_cases": data.get("historical_direction_memory_cases"),
            "option_positioning": {
                key: option_positioning.get(key)
                for key in (
                    "status",
                    "sample_bucket_at",
                    "previous_sample_bucket_at",
                    "age_minutes",
                    "nearest_expiry",
                    "underlying_price",
                    "contract_count",
                    "oi_contracts",
                    "ce_total_oi",
                    "pe_total_oi",
                    "put_call_oi_ratio",
                    "ce_total_oi_change_from_previous_bucket",
                    "pe_total_oi_change_from_previous_bucket",
                    "top_ce_oi",
                    "top_pe_oi",
                    "direction",
                    "counts_for_direction",
                    "directional_inference",
                    "futures_oi_required",
                )
            },
            "news": {
                key: news.get(key)
                for key in (
                    "status",
                    "visible_count",
                    "transmitted_count",
                    "counts",
                    "pit_basis",
                    "directional_vote_policy",
                )
            },
            "global_context": data.get("global_context") or {},
            "futures_oi": data.get("futures_oi"),
        },
    }


def replay_first_live_click_from_frozen_state(live_inputs: dict) -> dict:
    """Exercise today's decision code on the exact causal state frozen at 23:02.

    Only new inputs whose observation/collection provenance was already <= the
    original click may be attached. Because the durable news first-detected store
    did not exist before this click, retrospective news backfill is prohibited.
    Raw option OI is admitted as context but cannot vote under the current contract.
    """
    if not isinstance(live_inputs, dict) or live_inputs.get("point_in_time") is not True:
        raise ValueError("Point-in-time live inputs are required")
    if live_inputs.get("as_of") != FIRST_LIVE_CLICK_AT:
        raise ValueError("Live inputs are not anchored to the frozen first live click")

    option = deepcopy(live_inputs.get("option_positioning") or {})
    news = deepcopy(live_inputs.get("news") or {})
    if int(news.get("visible_count") or 0) != 0:
        raise ValueError("Retrospective news may not be admitted to the first live click")
    if option.get("counts_for_direction") is not False:
        raise ValueError("Raw option OI must remain non-voting in this comparison")

    current_decision = build_current_mind_decision(
        board={"groups": {}},
        regime=deepcopy(_FROZEN_REGIME),
        evidence=deepcopy(_FROZEN_EVIDENCE),
        scenario={},
        memory={},
        market={},
    )

    families = deepcopy(_FROZEN_V2_FAMILIES)
    families["PARTICIPATION"]["detail"]["option_positioning_available"] = (
        option.get("status") == "AVAILABLE"
    )
    families["PARTICIPATION"]["detail"]["option_positioning"] = option
    thesis = _thesis(list(families.values()))

    current = {
        "mode": "CRUDE_OIL_MINI_FIRST_LIVE_CLICK_FROZEN_STATE_REPLAY_V2",
        "click_at": FIRST_LIVE_CLICK_AT,
        "reference_contract": FIRST_LIVE_REFERENCE_CONTRACT,
        "latest_completed_bar_available_at": FIRST_LIVE_BASELINE["latest_completed_bar_available_at"],
        "current_mind": {
            "action": current_decision.get("action"),
            "direction": current_decision.get("direction"),
            "reason": current_decision.get("reason"),
            "evidence_quality": current_decision.get("evidence_quality"),
            "playbook": current_decision.get("playbook"),
        },
        "integrated_v2_shadow": {
            "direction": thesis.get("direction"),
            "confidence": thesis.get("confidence"),
            "thesis_state": thesis.get("state"),
            "supporting_families": thesis.get("supporting_families") or [],
            "opposing_families": thesis.get("opposing_families") or [],
            "families": families,
        },
        "data": {
            "candles": FIRST_LIVE_BASELINE["data"]["candles"],
            "historical_direction_memory_cases": FIRST_LIVE_BASELINE["data"]["historical_direction_memory_cases"],
            "option_positioning": option,
            "news": news,
            "global_context": {
                "WTI_CRUDE": "AVAILABLE",
                "BRENT_CRUDE": "AVAILABLE",
                "USDINR": "AVAILABLE",
                "DXY": "AVAILABLE",
            },
            "futures_oi": "OPTIONAL_SUPPORTING_CONTEXT_NOT_REQUIRED_FOR_OPTIONS_ONLY_SYSTEM",
        },
        "replay_basis": {
            "market_state": "EXACT_DERIVED_STATE_FROZEN_IN_ORIGINAL_23_02_ARTIFACT",
            "current_decision_code_exercised": True,
            "historical_candles_refetched": False,
            "why_not_refetch": "Avoid provider-history drift and future/revised-data contamination.",
            "option_input": "PERSISTED_PIT_SNAPSHOT_AS_OF_ORIGINAL_CLICK",
            "news_input": "NO_RETROSPECTIVE_BACKFILL",
            "known_post_click_outcome_used": False,
        },
    }
    return current


def build_first_live_click_comparison(current_result: dict) -> dict:
    """Compare current AlphaPilot to the frozen first live click without hindsight."""
    current = _decision_view(current_result)
    if current.get("click_at") != FIRST_LIVE_CLICK_AT:
        raise ValueError("Current result is not evaluated at the frozen first live click")

    baseline = deepcopy(FIRST_LIVE_BASELINE)
    old_current = baseline["current_mind"]
    new_current = current["current_mind"]
    old_v2 = baseline["integrated_v2_shadow"]
    new_v2 = current["integrated_v2_shadow"]

    option = current["data"]["option_positioning"]
    news = current["data"]["news"]
    return {
        "mode": "CRUDE_OIL_MINI_FIRST_LIVE_CLICK_COUNTERFACTUAL_V2",
        "comparison_click_at": FIRST_LIVE_CLICK_AT,
        "baseline": baseline,
        "current": current,
        "changes": {
            "current_mind_action_changed": old_current.get("action") != new_current.get("action"),
            "current_mind_direction_changed": old_current.get("direction") != new_current.get("direction"),
            "current_mind_reason_changed": old_current.get("reason") != new_current.get("reason"),
            "v2_direction_changed": old_v2.get("direction") != new_v2.get("direction"),
            "v2_confidence_changed": old_v2.get("confidence") != new_v2.get("confidence"),
        },
        "pit_audit": {
            "same_frozen_click": True,
            "future_market_outcome_used_as_input": False,
            "historical_candles_refetched": False,
            "retrospective_news_backfill_allowed": False,
            "news_pit_basis": news.get("pit_basis"),
            "option_snapshot_status": option.get("status"),
            "option_snapshot_directional_vote_enabled": bool(option.get("counts_for_direction")),
            "futures_oi_required": bool(option.get("futures_oi_required")),
        },
        "interpretation_policy": (
            "Any changed decision is descriptive only. Do not tune thresholds or promote "
            "option-OI/news rules from this known-outcome click."
        ),
    }
