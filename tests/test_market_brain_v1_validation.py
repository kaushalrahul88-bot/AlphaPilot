from app.market_brain_v1_validation import build_market_brain_v1_validation


def _data_integrity(ok=True):
    return {
        "mode": "COPPER_CURRENT_MIND_DATA_INTEGRITY_V1",
        "reference_contract": {"trading_symbol": "COPPER31AUG26FUT"},
        "checks": {"monotonic": ok, "on_grid": ok, "non_negative_volume": ok},
    }


def _direction():
    return {
        "mode": "COPPER_MARKET_BRAIN_DIRECTION_AUDIT_V1",
        "same_session_only": True,
        "trade_instrument": "OPTIONS",
        "underlying_reference_role": "REFERENCE_ONLY",
        "futures_pnl_calculated": False,
        "synthetic_option_premium_used": False,
        "brains": {
            "A": {
                "30": {"observations": 10, "direction_accuracy_pct": 50, "avg_signed_forward_pct": 0.01},
                "60": {"observations": 10, "direction_accuracy_pct": 52, "avg_signed_forward_pct": 0.02},
                "120": {"observations": 8, "direction_accuracy_pct": 55, "avg_signed_forward_pct": 0.03},
            },
            "B": {
                "30": {"observations": 8, "direction_accuracy_pct": 55, "avg_signed_forward_pct": 0.02},
                "60": {"observations": 8, "direction_accuracy_pct": 57, "avg_signed_forward_pct": 0.03},
                "120": {"observations": 7, "direction_accuracy_pct": 60, "avg_signed_forward_pct": 0.04},
            },
        },
    }


def _memory():
    return {
        "mode": "COPPER_EXPERIENCE_MEMORY_V1",
        "experiences": 120,
        "walk_forward_queries": 80,
        "analogue_k": 75,
        "production_rules_changed": False,
    }


def _ablation():
    return {"mode": "COPPER_CONTEXT_ABLATION_V1", "variants": {"baseline": {}, "full": {}}}


def test_missing_abstention_is_explicit_promotion_blocker():
    result = build_market_brain_v1_validation(
        data_integrity=_data_integrity(),
        direction_audit=_direction(),
        experience_memory=_memory(),
        context_ablation=_ablation(),
    )
    assert result["promotion_status"] == "NOT_READY"
    assert result["blockers"] == ["abstention_quality"]
    assert result["dimensions"]["abstention_quality"]["status"] == "MISSING_REQUIRED_EVIDENCE"
    assert result["production_rules_changed"] is False


def test_future_abstention_evidence_completes_measurement_without_auto_promotion():
    abstention = {
        "mode": "COPPER_MARKET_BRAIN_ABSTENTION_AUDIT_V1",
        "no_trade_observations": 100,
        "no_trade_followed_by_large_move": 20,
        "large_move_threshold_pct": 0.30,
        "gap_counts": {"PERCEPTION_GAP": 7},
    }
    result = build_market_brain_v1_validation(
        data_integrity=_data_integrity(),
        direction_audit=_direction(),
        experience_memory=_memory(),
        context_ablation=_ablation(),
        abstention_audit=abstention,
    )
    assert result["blockers"] == []
    assert result["promotion_status"] == "VALIDATION_EVIDENCE_COMPLETE_NOT_PROMOTED"
    assert result["dimensions"]["abstention_quality"]["no_trade_followed_by_large_move"] == 20


def test_point_in_time_failure_blocks_readiness():
    result = build_market_brain_v1_validation(
        data_integrity=_data_integrity(ok=False),
        direction_audit=_direction(),
        experience_memory=_memory(),
        context_ablation=_ablation(),
        abstention_audit={"mode": "X"},
    )
    assert result["dimensions"]["point_in_time_integrity"]["status"] == "FAILED"
    assert "point_in_time_integrity" in result["blockers"]


def test_execution_stage_separation_is_fail_closed():
    direction = _direction()
    direction["futures_pnl_calculated"] = True
    result = build_market_brain_v1_validation(
        data_integrity=_data_integrity(),
        direction_audit=direction,
        experience_memory=_memory(),
        context_ablation=_ablation(),
        abstention_audit={"mode": "X"},
    )
    assert result["dimensions"]["execution_separation"]["status"] == "FAILED"
    assert "execution_separation" in result["blockers"]


def test_simple_vs_richer_brain_difference_is_reported_not_promoted():
    result = build_market_brain_v1_validation(
        data_integrity=_data_integrity(),
        direction_audit=_direction(),
        experience_memory=_memory(),
        context_ablation=_ablation(),
    )
    horizon = result["dimensions"]["direction_reading"]["horizons"]["60"]
    assert horizon["brain_b_minus_a"]["direction_accuracy_pct_points"] == 5.0
    assert horizon["brain_b_minus_a"]["avg_signed_forward_pct"] == 0.01
