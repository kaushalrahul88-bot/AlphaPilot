from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.copper_commodity_brain_prospective_store_v1 import build_prospective_record
from app.copper_commodity_brain_prospective_v1 import (
    STREAM_ID,
    evaluate_copper_commodity_brain_prospective,
    validate_prospective_board,
)

IST = ZoneInfo("Asia/Kolkata")
AS_OF = datetime(2026, 9, 7, 10, 0, tzinfo=IST)


def board() -> dict:
    return {
        "status": "AVAILABLE",
        "model_id": "COPPER_PIT_INFORMATION_BOARD_V2",
        "product": "COPPER",
        "trade_instrument": "OPTIONS_ONLY",
        "as_of": AS_OF.isoformat(),
        "groups": {
            "primary_market": {
                "MCX_COPPER": {
                    "status": "AVAILABLE",
                    "trading_symbol": "COPPER30SEP26FUT",
                    "visible_candles": 51,
                    "first_seen_immutable": True,
                    "historical_backfill_used": False,
                    "mutable_generic_fallback_used": False,
                    "perception_snapshot": {
                        "structure": "UPTREND",
                        "return_15m_pct": 0.10,
                        "return_60m_pct": 0.20,
                    },
                }
            },
            "option_market": {
                "MCX_COPPER_OPTION": {
                    "status": "AVAILABLE",
                    "first_seen_immutable": True,
                    "historical_backfill_used": False,
                    "mutable_generic_fallback_used": False,
                    "participation_snapshot": {
                        "status": "READY",
                        "rule_version": "COPPER_OPTION_PARTICIPATION_V1",
                        "latest_bucket_at": "2026-09-07T09:55:00+05:30",
                        "previous_bucket_at": "2026-09-07T09:50:00+05:30",
                        "bucket_gap_seconds": 300,
                        "nearest_expiry": "2026-09-30",
                        "matched_contracts": 2,
                        "matched_ce_contracts": 1,
                        "matched_pe_contracts": 1,
                        "contract_evidence": [
                            {
                                "trading_symbol": "COPPER_CE",
                                "option_type": "CE",
                                "eligible_new_oi_evidence": True,
                                "stance": "BULLISH",
                            },
                            {
                                "trading_symbol": "COPPER_PE",
                                "option_type": "PE",
                                "eligible_new_oi_evidence": True,
                                "stance": "BULLISH",
                            },
                        ],
                    },
                }
            },
            "global_copper": {},
            "china_macro": {},
            "news": {},
            "currency": {},
            "positioning": {},
            "experience_memory": {
                "DIRECTION_MEMORY": {
                    "status": "AVAILABLE",
                    "registered_directional_stance": "BULLISH",
                    "direction_vote_registered": True,
                }
            },
        },
        "historical_backfill_used": False,
        "sealed_copper_current_mind_effect": "NONE",
        "direction_v2_effect": "NONE",
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
    }


def test_prospective_shared_brain_uses_only_immutable_board_and_memory_is_context():
    evaluation = evaluate_copper_commodity_brain_prospective(board())

    assert evaluation["prospective_stream_id"] == STREAM_ID
    assert evaluation["evaluation_class"] == "PROSPECTIVE_SHADOW"
    assert evaluation["prospective"] is True
    assert evaluation["input_provenance"]["status"] == "VALID"
    assert evaluation["input_provenance"]["market_trading_symbol"] == "COPPER30SEP26FUT"
    assert evaluation["direction"] == "BULLISH"
    assert evaluation["direction_confidence"] == "MODERATE"
    assert set(evaluation["supporting_families"]) == {"LOCAL_STRUCTURE", "OPTION_PARTICIPATION"}
    memory = evaluation["families"]["EXPERIENCE_MEMORY"]
    assert memory["stance"] == "BULLISH"
    assert memory["counts_for_direction"] is False
    assert memory["depends_on_origins"] == ["LOCAL_PRICE_STRUCTURE"]
    assert evaluation["sealed_current_mind_effect"] == "NONE"
    assert evaluation["capital_committed"] == 0
    assert evaluation["forward_outcome_data_read"] is False
    assert evaluation["pnl_read"] is False


def test_mutable_or_reconstructed_available_tape_is_rejected():
    bad = board()
    bad["groups"]["primary_market"]["MCX_COPPER"]["historical_backfill_used"] = True
    validation = validate_prospective_board(bad)
    assert validation["status"] == "INVALID"
    assert "MARKET_HISTORICAL_BACKFILL_FORBIDDEN" in validation["violations"]
    with pytest.raises(ValueError):
        evaluate_copper_commodity_brain_prospective(bad)


def test_prospective_record_is_exact_now_outcome_blind_and_separate_from_direction_v2():
    current = board()
    evaluation = evaluate_copper_commodity_brain_prospective(current)
    record = build_prospective_record(current, evaluation, evaluated_at=AS_OF)

    assert record["model_id"] == STREAM_ID
    assert record["contract_version"] == "COPPER_COMMODITY_BRAIN_SHARED_SHADOW_V1"
    assert record["first_seen_immutable"] is True
    assert record["outcome_fields_stored"] is False
    assert record["historical_as_of_allowed"] is False
    assert record["prospective_memory_eligible"] is False
    assert record["board_as_of"] == AS_OF.isoformat()
    assert record["evaluation_snapshot"]["sealed_current_mind_effect"] == "NONE"
    assert record["evaluation_snapshot"]["forward_outcome_data_read"] is False
    assert record["evaluation_snapshot"]["pnl_read"] is False


def test_historical_as_of_cannot_be_inserted_as_prospective():
    current = board()
    evaluation = evaluate_copper_commodity_brain_prospective(current)
    with pytest.raises(ValueError, match="exactly-now"):
        build_prospective_record(
            current,
            evaluation,
            evaluated_at=AS_OF + timedelta(minutes=5),
        )


def test_outcome_fields_cannot_enter_immutable_shared_ledger():
    current = board()
    current["outcome"] = {"direction_correct": True}
    evaluation = evaluate_copper_commodity_brain_prospective(current)
    with pytest.raises(ValueError, match="Future/outcome field"):
        build_prospective_record(current, evaluation, evaluated_at=AS_OF)
