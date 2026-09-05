from __future__ import annotations

from .copper_commodity_brain_shadow_v1 import (
    CONTRACT_VERSION as BRAIN_CONTRACT_VERSION,
    evaluate_copper_commodity_brain_shadow,
)

STREAM_ID = "COPPER_COMMODITY_BRAIN_SHARED_PROSPECTIVE_V1"
EVALUATION_CLASS = "PROSPECTIVE_SHADOW"


def _market_tape(board: dict) -> dict:
    return ((((board.get("groups") or {}).get("primary_market") or {}).get("MCX_COPPER")) or {})


def _option_tape(board: dict) -> dict:
    return ((((board.get("groups") or {}).get("option_market") or {}).get("MCX_COPPER_OPTION")) or {})


def validate_prospective_board(board: dict) -> dict:
    """Validate that a live board contains no reconstructive Copper tape.

    Warming-up/unavailable evidence is allowed to remain unavailable. What is not
    allowed is silently substituting mutable generic or historical reconstructed
    market/option data into a prospective shared-brain observation.
    """
    market = _market_tape(board)
    option = _option_tape(board)
    violations: list[str] = []

    if board.get("historical_backfill_used") is True:
        violations.append("BOARD_HISTORICAL_BACKFILL_FORBIDDEN")
    if market.get("status") == "AVAILABLE":
        if market.get("first_seen_immutable") is not True:
            violations.append("MARKET_TAPE_NOT_FIRST_SEEN_IMMUTABLE")
        if market.get("historical_backfill_used") is True:
            violations.append("MARKET_HISTORICAL_BACKFILL_FORBIDDEN")
        if market.get("mutable_generic_fallback_used") is True:
            violations.append("MARKET_MUTABLE_GENERIC_FALLBACK_FORBIDDEN")
    if option.get("status") == "AVAILABLE":
        if option.get("first_seen_immutable") is not True:
            violations.append("OPTION_TAPE_NOT_FIRST_SEEN_IMMUTABLE")
        if option.get("historical_backfill_used") is True:
            violations.append("OPTION_HISTORICAL_BACKFILL_FORBIDDEN")
        if option.get("mutable_generic_fallback_used") is True:
            violations.append("OPTION_MUTABLE_GENERIC_FALLBACK_FORBIDDEN")

    return {
        "status": "VALID" if not violations else "INVALID",
        "violations": violations,
        "market_status": market.get("status"),
        "market_first_seen_immutable": market.get("first_seen_immutable") is True,
        "market_trading_symbol": market.get("trading_symbol"),
        "market_visible_candles": market.get("visible_candles"),
        "option_status": option.get("status"),
        "option_first_seen_immutable": option.get("first_seen_immutable") is True,
        "historical_backfill_used": False,
        "mutable_generic_fallback_used": False,
    }


def evaluate_copper_commodity_brain_prospective(board: dict) -> dict:
    provenance = validate_prospective_board(board or {})
    if provenance["status"] != "VALID":
        raise ValueError(
            "Copper shared prospective board failed provenance validation: "
            + ",".join(provenance["violations"])
        )

    evaluation = dict(evaluate_copper_commodity_brain_shadow(board or {}))
    evaluation.update(
        {
            "prospective_stream_id": STREAM_ID,
            "evaluation_class": EVALUATION_CLASS,
            "prospective": True,
            "outcome_blind_at_decision_time": True,
            "future_return_read": False,
            "pnl_read": False,
            "historical_backfill_used": False,
            "prospective_memory_eligible": False,
            "promotion_eligible": False,
            "input_provenance": provenance,
        }
    )
    evaluation["integration_contract"] = {
        **dict(evaluation.get("integration_contract") or {}),
        "version": BRAIN_CONTRACT_VERSION,
        "prospective_stream_id": STREAM_ID,
        "evaluation_class": EVALUATION_CLASS,
        "prospective": True,
        "requires_first_seen_immutable_market_tape": True,
        "requires_first_seen_immutable_option_tape_when_available": True,
        "historical_reconstruction_allowed": False,
        "mutable_generic_fallback_allowed": False,
        "sealed_copper_current_mind_effect": "NONE",
        "direction_v2_history_effect": "NONE",
        "outcome_join_at_decision_time": False,
        "pnl_join_at_decision_time": False,
        "promotion_allowed": False,
    }
    return evaluation
