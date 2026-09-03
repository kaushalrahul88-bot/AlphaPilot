from __future__ import annotations

from copy import deepcopy


FIRST_LIVE_CLICK_AT = "2026-09-03T23:02:01.916091+05:30"
FIRST_LIVE_REFERENCE_CONTRACT = "CRUDEOILM21SEP26FUT"

# Frozen from the original live artifact. These values are comparison inputs only;
# they are never fed back into the current decision engine.
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


def build_first_live_click_comparison(current_result: dict) -> dict:
    """Compare current AlphaPilot to the frozen first live click without hindsight.

    The caller must evaluate the current engine at FIRST_LIVE_CLICK_AT using only
    point-in-time inputs. This helper never changes a decision or uses the later
    market outcome as an input.
    """
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
        "mode": "CRUDE_OIL_MINI_FIRST_LIVE_CLICK_COUNTERFACTUAL_V1",
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
