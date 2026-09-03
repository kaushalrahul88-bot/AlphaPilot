from app.crude_oil_mini_first_live_click_compare import (
    FIRST_LIVE_CLICK_AT,
    build_first_live_click_comparison,
    replay_first_live_click_from_frozen_state,
)


def _live_inputs():
    return {
        "as_of": FIRST_LIVE_CLICK_AT,
        "point_in_time": True,
        "futures_oi_required": False,
        "option_positioning": {
            "status": "AVAILABLE",
            "sample_bucket_at": "2026-09-03T17:25:00+00:00",
            "previous_sample_bucket_at": "2026-09-03T17:10:00+00:00",
            "age_minutes": 7.032,
            "nearest_expiry": "2026-09-17",
            "underlying_price": 8670.0,
            "contract_count": 6,
            "oi_contracts": 6,
            "ce_total_oi": 32664.0,
            "pe_total_oi": 48413.0,
            "put_call_oi_ratio": 1.4821516042125888,
            "ce_total_oi_change_from_previous_bucket": -1770.0,
            "pe_total_oi_change_from_previous_bucket": 1874.0,
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
            "counts": {"ALLOW": 0, "CONTEXT_ONLY": 0, "BLOCK": 0},
            "pit_basis": "FIRST_DETECTED_AT",
            "directional_vote_policy": "NO_HEADLINE_ONLY_VOTE; EVENT_REACTION_CONFIRMATION_REQUIRED",
            "records": [],
            "event_records": [],
        },
    }


def test_frozen_state_replay_exercises_current_decision_code_without_refetch():
    current = replay_first_live_click_from_frozen_state(_live_inputs())
    assert current["current_mind"]["action"] == "NO_TRADE"
    assert current["current_mind"]["reason"] == "EVIDENCE_NOT_COHERENT"
    assert current["current_mind"]["evidence_quality"] == "CONFLICTED"
    assert current["integrated_v2_shadow"]["direction"] == "UNKNOWN"
    assert current["integrated_v2_shadow"]["confidence"] == "WEAK"
    assert current["integrated_v2_shadow"]["supporting_families"] == ["GLOBAL_CRUDE"]
    assert current["replay_basis"]["current_decision_code_exercised"] is True
    assert current["replay_basis"]["historical_candles_refetched"] is False
    assert current["data"]["option_positioning"]["ce_total_oi"] == 32664.0
    assert current["data"]["option_positioning"]["pe_total_oi"] == 48413.0


def test_first_live_click_comparison_keeps_known_outcome_out_of_inputs():
    current = replay_first_live_click_from_frozen_state(_live_inputs())
    result = build_first_live_click_comparison(current)
    assert result["comparison_click_at"] == FIRST_LIVE_CLICK_AT
    assert result["pit_audit"]["same_frozen_click"] is True
    assert result["pit_audit"]["future_market_outcome_used_as_input"] is False
    assert result["pit_audit"]["historical_candles_refetched"] is False
    assert result["pit_audit"]["retrospective_news_backfill_allowed"] is False
    assert result["pit_audit"]["option_snapshot_directional_vote_enabled"] is False
    assert result["pit_audit"]["futures_oi_required"] is False
    assert result["baseline"]["current_mind"]["action"] == "NO_TRADE"
    assert result["changes"]["current_mind_action_changed"] is False
    assert result["changes"]["v2_direction_changed"] is False


def test_frozen_state_replay_rejects_retroactive_news():
    inputs = _live_inputs()
    inputs["news"]["visible_count"] = 1
    try:
        replay_first_live_click_from_frozen_state(inputs)
    except ValueError as exc:
        assert "Retrospective news" in str(exc)
    else:
        raise AssertionError("Expected replay to reject retrospective news")


def test_first_live_click_comparison_rejects_different_click():
    current = replay_first_live_click_from_frozen_state(_live_inputs())
    current["click_at"] = "2026-09-03T23:15:00+05:30"
    try:
        build_first_live_click_comparison(current)
    except ValueError as exc:
        assert "frozen first live click" in str(exc)
    else:
        raise AssertionError("Expected comparison to reject a different click")
