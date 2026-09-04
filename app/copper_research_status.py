from __future__ import annotations

from .copper_direction_brain_v2_shadow import evaluate_copper_direction_v2_shadow
from .copper_pit_information_board_v2 import read_copper_information_board
from .current_mind_copper_forward import (
    FORWARD_EXPECTED_CLICKS,
    FORWARD_MODE,
    FORWARD_SCORE_END,
    FORWARD_SCORE_START,
)


MODEL_ID = "COPPER_RESEARCH_STATUS_V1"


def build_copper_research_status(board: dict, direction_v2: dict) -> dict:
    market = (((board.get("groups") or {}).get("primary_market") or {}).get("MCX_COPPER") or {})
    options = (((board.get("groups") or {}).get("option_market") or {}).get("MCX_COPPER_OPTION") or {})
    return {
        "status": "ACTIVE" if board.get("status") == "AVAILABLE" else "WARMING_UP",
        "model_id": MODEL_ID,
        "product": "COPPER",
        "trade_instrument": "OPTIONS_ONLY",
        "information_board": board,
        "direction_v2_shadow": direction_v2,
        "prospective_data": {
            "candle_tape_status": market.get("status"),
            "visible_5m_candles": int(market.get("visible_candles") or 0),
            "candle_first_seen_immutable": bool(market.get("first_seen_immutable")),
            "candle_provenance_id": market.get("provenance_id"),
            "latest_candle_available_at": market.get("available_at"),
            "option_tape_status": options.get("status"),
            "option_contracts_visible": int(options.get("contracts_visible") or 0),
            "option_first_seen_immutable": bool(options.get("first_seen_immutable")),
            "option_provenance_id": options.get("provenance_id"),
            "latest_option_available_at": options.get("available_at"),
            "historical_backfill_used": False,
        },
        "sealed_forward_phase1": {
            "mode": FORWARD_MODE,
            "score_start": FORWARD_SCORE_START.isoformat(),
            "score_end": FORWARD_SCORE_END.isoformat(),
            "expected_clicks": FORWARD_EXPECTED_CLICKS,
            "decision_rules_frozen": True,
            "interim_score_exposed_by_status_endpoint": False,
            "score_visibility": "SEALED_BY_FORWARD_WORKFLOW_UNTIL_PREREGISTERED_PHASE_COMPLETES",
            "current_mind_changed_by_v2": False,
        },
        "pipeline": {
            "immutable_market_tape": "ACTIVE" if market.get("status") == "AVAILABLE" else "WARMING_UP",
            "immutable_option_tape": "ACTIVE" if options.get("status") == "AVAILABLE" else "WARMING_UP",
            "pit_information_board": "ACTIVE",
            "current_mind_phase1": "FROZEN_SCORE_SEALED",
            "direction_v2": "SHADOW_ONLY",
            "option_oi_premium_model": "NOT_REGISTERED",
            "experience_memory_v2": "NOT_CONNECTED",
            "promotion": "LOCKED",
        },
        "research_only": True,
        "paper_signal_only": True,
        "production_rules_changed": False,
        "sealed_current_mind_effect": "NONE",
        "direction_v2_effect": "NONE",
        "option_expression_effect": "NONE",
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
        "promotion_eligible": False,
    }


async def read_copper_research_status(database_url: str, *, as_of=None) -> dict:
    board = await read_copper_information_board(database_url, as_of=as_of)
    direction_v2 = evaluate_copper_direction_v2_shadow(board)
    return build_copper_research_status(board, direction_v2)
