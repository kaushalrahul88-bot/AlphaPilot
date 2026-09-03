from app.crude_oil_mini_first_live_click_compare import (
    FIRST_LIVE_CLICK_AT,
    build_first_live_click_comparison,
)


def _current_result():
    return {
        "mode": "CRUDE_OIL_MINI_CURRENT_MIND_LIVE_SHADOW_V2_OPTION_OI_NEWS",
        "click_at": FIRST_LIVE_CLICK_AT,
        "reference_contract": "CRUDEOILM21SEP26FUT",
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
            "option_positioning": {
                "status": "AVAILABLE",
                "sample_bucket_at": "2026-09-03T22:55:00+05:30",
                "previous_sample_bucket_at": "2026-09-03T22:50:00+05:30",
                "age_minutes": 7.032,
                "nearest_expiry": "2026-09-17",
                "underlying_price": 8662.0,
                "contract_count": 6,
                "oi_contracts": 6,
                "ce_total_oi": 100.0,
                "pe_total_oi": 120.0,
                "put_call_oi_ratio": 1.2,
                "ce_total_oi_change_from_previous_bucket": 5.0,
                "pe_total_oi_change_from_previous_bucket": 8.0,
                "top_ce_oi": [],
                "top_pe_oi": [],
                "direction": "UNKNOWN",
                "counts_for_direction": False,
                "directional_inference": "WITHHELD_UNTIL_OPTION_OI_PREMIUM_CAUSAL_RULE_IS_PREREGISTERED",
                "futures_oi_required": False,
            },
            "news": {
                "status": "UNAVAILABLE",
                "visible_count": 0,
                "transmitted_count": 0,
                "counts": {},
                "pit_basis": "FIRST_DETECTED_AT",
                "directional_vote_policy": "NO_HEADLINE_ONLY_VOTE; EVENT_REACTION_CONFIRMATION_REQUIRED",
            },
            "global_context": {
                "WTI_CRUDE": "AVAILABLE",
                "BRENT_CRUDE": "AVAILABLE",
                "USDINR": "AVAILABLE",
                "DXY": "AVAILABLE",
            },
            "futures_oi": "OPTIONAL_SUPPORTING_CONTEXT_NOT_REQUIRED_FOR_OPTIONS_ONLY_SYSTEM",
        },
    }


def test_first_live_click_comparison_keeps_known_outcome_out_of_inputs():
    result = build_first_live_click_comparison(_current_result())
    assert result["comparison_click_at"] == FIRST_LIVE_CLICK_AT
    assert result["pit_audit"]["same_frozen_click"] is True
    assert result["pit_audit"]["future_market_outcome_used_as_input"] is False
    assert result["pit_audit"]["retrospective_news_backfill_allowed"] is False
    assert result["pit_audit"]["option_snapshot_directional_vote_enabled"] is False
    assert result["pit_audit"]["futures_oi_required"] is False
    assert result["baseline"]["current_mind"]["action"] == "NO_TRADE"
    assert result["changes"]["current_mind_action_changed"] is False


def test_first_live_click_comparison_rejects_different_click():
    current = _current_result()
    current["click_at"] = "2026-09-03T23:15:00+05:30"
    try:
        build_first_live_click_comparison(current)
    except ValueError as exc:
        assert "frozen first live click" in str(exc)
    else:
        raise AssertionError("Expected comparison to reject a different click")
