from __future__ import annotations

from .commodity_direction_core import (
    architecture_contract as shared_core_contract,
    build_direction_thesis,
    normalize_family,
)
from .copper_direction_brain_v2_shadow import (
    china_demand_family,
    context_modifiers,
    event_reaction_family,
    experience_memory_family,
    global_copper_family,
    local_structure_family,
)
from .copper_direction_brain_v2_shadow_v2 import option_participation_family
from .copper_option_participation_v1 import RULE_VERSION as OPTION_PARTICIPATION_RULE_VERSION

MODE = "COPPER_COMMODITY_BRAIN_SHARED_SHADOW_V1"
CONTRACT_VERSION = "COPPER_COMMODITY_BRAIN_SHARED_SHADOW_V1"


def _adapt_local(board: dict) -> dict:
    return normalize_family(
        local_structure_family(board),
        role="PERCEPTION",
        independence_status="INDEPENDENT",
    )


def _adapt_participation(board: dict) -> dict:
    return normalize_family(
        option_participation_family(board),
        role="POSITIONING",
        independence_status="INDEPENDENT",
    )


def _adapt_global(board: dict) -> dict:
    # The current Copper builder deliberately abstains until a timestamp-safe
    # first-seen COMEX/LME tape exists. The shared core must preserve that abstention.
    return normalize_family(
        global_copper_family(board),
        role="PERCEPTION",
        independence_status="INDEPENDENT",
    )


def _adapt_china(board: dict) -> dict:
    # Slow macro levels remain context only until a separately frozen surprise +
    # reaction rule exists. Absolute PMI or similar levels cannot create a vote.
    return normalize_family(
        china_demand_family(board),
        role="CONTEXT",
        force_context_only=True,
    )


def _adapt_event(board: dict) -> dict:
    # The current builder is non-voting. If a future Copper event lifecycle is
    # registered, its reaction dependencies must be declared before it may vote.
    return normalize_family(
        event_reaction_family(board),
        role="EVENT",
        independence_status="INDEPENDENT",
    )


def _adapt_memory(board: dict) -> dict:
    legacy = experience_memory_family(board)
    detail = dict(legacy.get("detail") or {})
    detail.update(
        {
            "shared_core_role": "MEMORY_CONTEXT",
            "shared_core_directional_vote_allowed": False,
            "reason": (
                "Experience/Memory is context in Shared Commodity Brain V1. "
                "A local-price-derived analogue cannot manufacture an independent causal confirmation."
            ),
        }
    )
    legacy = dict(legacy)
    legacy["detail"] = detail
    return normalize_family(
        legacy,
        role="MEMORY",
        force_context_only=True,
        depends_on_origins=["LOCAL_PRICE_STRUCTURE"],
    )


def build_copper_families(board: dict) -> list[dict]:
    return [
        _adapt_local(board),
        _adapt_participation(board),
        _adapt_global(board),
        _adapt_china(board),
        _adapt_event(board),
        _adapt_memory(board),
    ]


def evaluate_copper_commodity_brain_shadow(board: dict) -> dict:
    families = build_copper_families(board)
    thesis = build_direction_thesis(families, minimum_confirmations=2)
    dependency = thesis["dependency_audit"]

    return {
        "mode": MODE,
        "contract_version": CONTRACT_VERSION,
        "shared_core_version": shared_core_contract()["version"],
        "product": "COPPER",
        "trade_instrument": "OPTIONS_ONLY",
        "as_of": board.get("as_of"),
        "evaluation_class": "SHADOW",
        "research_only": True,
        "shadow_only": True,
        "direction": thesis["direction"],
        "direction_confidence": thesis["confidence"],
        "thesis_state": thesis["state"],
        "supporting_families": thesis["supporting_families"],
        "opposing_families": thesis["opposing_families"],
        "counted_families": dependency["counted_families"],
        "counted_origins": dependency["counted_origins"],
        "dependency_audit": dependency,
        "families": {row["family"]: row for row in families},
        "modifiers": context_modifiers(board),
        "experience_memory_role": "CONTEXT_NOT_INDEPENDENT_CONFIRMATION",
        "current_mind_action": None,
        "entry_readiness": "NOT_EVALUATED_DIRECTION_ONLY",
        "setup_geometry_generated": False,
        "option_expression_generated": False,
        "sealed_current_mind_effect": "NONE",
        "decision_effect": "NONE",
        "option_expression_effect": "NONE",
        "production_rules_changed": False,
        "historical_records_rewritten": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
        "promotion_eligible": False,
        "rules": [
            "Commodity-specific modules construct evidence; the shared core owns causal voting and thesis semantics.",
            "No weighted indicator score is used.",
            "At least two independent causal origins must align and no counted independent origin may oppose them.",
            "Duplicate or declared-dependent origins cannot manufacture extra confirmation.",
            "Local structure and local momentum remain one LOCAL_PRICE_STRUCTURE origin.",
            "Copper Option Participation uses its existing frozen OI-plus-premium change rule and remains one OPTION_MARKET_POSITIONING origin.",
            "Raw option OI levels, OI-flat/decreasing contracts and underlying-price direction cannot manufacture Option Participation evidence.",
            "COMEX/LME remain unavailable for voting until timestamp-safe Copper global data is genuinely connected.",
            "China macro is context until a separately frozen event-surprise and reaction rule exists.",
            "Headline sentiment cannot vote direction.",
            "Experience/Memory is context in this shared V1 and cannot satisfy the independent-family gate.",
            "Direction does not imply setup validity, entry readiness, CE/PE selection or a trade.",
        ],
        "integration_contract": integration_contract(),
    }


def integration_contract() -> dict:
    return {
        "version": CONTRACT_VERSION,
        "shared_core": shared_core_contract(),
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_expression_effect": "NONE",
        "production_rules_changed": False,
        "old_copper_v1_v2_replaced": False,
        "stored_copper_predictions_rewritten": False,
        "causal_origin_deduplication": True,
        "causal_dependency_deduplication": True,
        "minimum_independent_confirmations": 2,
        "experience_memory_role": "CONTEXT_ONLY",
        "experience_memory_can_satisfy_confirmation_gate": False,
        "option_participation_rule_version": OPTION_PARTICIPATION_RULE_VERSION,
        "global_copper_requires_timestamp_safe_first_seen_data": True,
        "headline_sentiment_direction_allowed": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "promotion_allowed": False,
    }
