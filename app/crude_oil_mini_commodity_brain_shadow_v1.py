from __future__ import annotations

from .commodity_direction_core import architecture_contract, build_direction_thesis, normalize_family
from .crude_oil_mini_direction_brain_v2_integrated import (
    _fx_translation,
    _local_structure,
    _memory_family,
)
from .crude_oil_mini_event_reaction_v3 import build_event_reaction_family
from .crude_oil_mini_global_crude_perception_v2 import build_global_crude_perception
from .crude_oil_mini_participation_v2 import build_participation_observation

MODE = "CRUDE_OIL_MINI_COMMODITY_BRAIN_SHARED_SHADOW_V1"
LEGACY_MODE = "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_INTEGRATED_SHADOW"


def _normalize_crude_families(
    *,
    local: dict,
    participation: dict,
    global_crude: dict,
    event: dict,
    memory: dict,
) -> list[dict]:
    """Map Crude-specific evidence builders into the shared Commodity Brain contract.

    Direction Memory is deliberately visible but context-only. Its historical analogue
    search is conditioned on local-price state, so it cannot manufacture a second
    independent causal confirmation beside Local Structure.
    """
    return [
        normalize_family(local, role="PERCEPTION"),
        normalize_family(participation, role="POSITIONING"),
        normalize_family(global_crude, role="GLOBAL_MARKET"),
        normalize_family(event, role="EVENT"),
        normalize_family(
            memory,
            role="EXPERIENCE_CONTEXT",
            independence_status="DEPENDENT_CONTEXT_ONLY",
            depends_on_origins=["LOCAL_PRICE_STRUCTURE"],
            force_context_only=True,
        ),
    ]


def synthesize_crude_shared_families(
    *,
    local: dict,
    participation: dict,
    global_crude: dict,
    event: dict,
    memory: dict,
) -> dict:
    families = _normalize_crude_families(
        local=local,
        participation=participation,
        global_crude=global_crude,
        event=event,
        memory=memory,
    )
    thesis = build_direction_thesis(families, minimum_confirmations=2)
    return {
        "families": families,
        "thesis": thesis,
    }


def evaluate_crude_oil_mini_commodity_brain_shadow(
    *,
    click_timestamp: str,
    snapshot: dict,
    profile: dict,
    mini_candles,
    global_context_probe: dict,
    context_records: list[dict] | None = None,
    event_records: list[dict] | None = None,
    direction_memory_cases: list[dict] | None = None,
    option_positioning: dict | None = None,
) -> dict:
    """Evaluate Crude Mini through the shared Commodity Brain without changing actions."""
    local = _local_structure(snapshot or {})
    participation = build_participation_observation(
        mini_candles,
        click_timestamp=click_timestamp,
        snapshot=snapshot or {},
        profile=profile or {},
        option_positioning=option_positioning or {},
    )
    global_crude = build_global_crude_perception(global_context_probe or {}, click_timestamp)
    event = build_event_reaction_family(event_records or [], click_timestamp)
    memory = _memory_family(direction_memory_cases or [], snapshot or {}, click_timestamp)

    synthesis = synthesize_crude_shared_families(
        local=local,
        participation=participation,
        global_crude=global_crude,
        event=event,
        memory=memory,
    )
    families = synthesis["families"]
    thesis = synthesis["thesis"]
    fx = normalize_family(
        _fx_translation(context_records or [], click_timestamp, global_crude),
        role="MODIFIER",
        force_context_only=True,
    )

    return {
        "mode": MODE,
        "legacy_reference_mode": LEGACY_MODE,
        "research_only": True,
        "shadow_only": True,
        "decision_path_changed": False,
        "current_mind_action": None,
        "geometry_generated": False,
        "option_brain_action": None,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
        "promotion_allowed": False,
        "click_timestamp": click_timestamp,
        "direction": thesis["direction"],
        "direction_confidence": thesis["confidence"],
        "thesis_state": thesis["state"],
        "supporting_families": thesis["supporting_families"],
        "opposing_families": thesis["opposing_families"],
        "dependency_audit": thesis["dependency_audit"],
        "families": {row["family"]: row for row in families},
        "modifiers": {"FX_TRANSLATION": fx},
        "persistence": (memory.get("detail") or {}).get("persistence", "UNRESOLVED"),
        "entry_readiness": "NOT_EVALUATED_DIRECTION_ONLY",
        "rules": [
            "Crude-specific evidence builders are preserved; synthesis uses the shared Commodity Direction Core.",
            "At least two truly independent causal origins must align and no counted origin may oppose them.",
            "Direction Memory is Experience context and cannot independently satisfy the confirmation gate.",
            "WTI and Brent remain one correlated GLOBAL_CRUDE family.",
            "Event reaction dependencies remain auditable and are deduplicated against counted Local or Global origins.",
            "USDINR remains modifier-only context.",
            "Direction does not imply setup validity, option selection, execution, or capital deployment.",
        ],
        "integration_contract": integration_contract(),
    }


def integration_contract() -> dict:
    return {
        "version": MODE,
        "shared_core": architecture_contract(),
        "commodity_profile": "CRUDE_OIL_MINI",
        "legacy_reference_mode": LEGACY_MODE,
        "legacy_module_modified": False,
        "legacy_outputs_rewritten": False,
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_brain_effect": "NONE",
        "execution_effect": "NONE",
        "direction_memory_role": "EXPERIENCE_CONTEXT",
        "direction_memory_counts_as_independent_confirmation": False,
        "direction_memory_depends_on_origins": ["LOCAL_PRICE_STRUCTURE"],
        "minimum_independent_confirmations": 2,
        "weighted_score_used": False,
        "promotion_allowed": False,
    }
