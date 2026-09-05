from __future__ import annotations

MODE = "CRUDE_OIL_MINI_SHARED_BRAIN_PARITY_V1"
MEMORY_FAMILY = "DIRECTION_MEMORY"


def _summary(result: dict) -> dict:
    return {
        "mode": result.get("mode"),
        "direction": str(result.get("direction") or "UNKNOWN").upper(),
        "confidence": str(result.get("direction_confidence") or "UNKNOWN").upper(),
        "thesis_state": result.get("thesis_state"),
        "supporting_families": list(result.get("supporting_families") or []),
        "opposing_families": list(result.get("opposing_families") or []),
    }


def _legacy_memory_counted(legacy: dict) -> bool:
    family = ((legacy.get("families") or {}).get(MEMORY_FAMILY) or {})
    if family.get("counts_for_direction") is not True:
        return False
    audit = legacy.get("dependency_audit") or {}
    counted = audit.get("counted") or []
    return any(row.get("family") == MEMORY_FAMILY for row in counted if isinstance(row, dict))


def build_shared_brain_parity(*, legacy: dict, shared: dict) -> dict:
    """Compare two direction shadows produced from the same PIT click inputs.

    This diagnostic is descriptive only. It cannot change Current Mind, geometry,
    option expression, execution, capital, research promotion, or historical rows.
    """
    legacy_view = _summary(legacy or {})
    shared_view = _summary(shared or {})
    direction_agreement = legacy_view["direction"] == shared_view["direction"]
    confidence_agreement = legacy_view["confidence"] == shared_view["confidence"]

    if direction_agreement and confidence_agreement:
        divergence = "NONE"
    elif _legacy_memory_counted(legacy or {}):
        divergence = "MEMORY_CONTEXT_ONLY_CORRECTION"
    elif direction_agreement:
        divergence = "CONFIDENCE_OR_DEPENDENCY_AUDIT_DIFFERENCE"
    else:
        divergence = "SHARED_CORE_CAUSAL_SYNTHESIS_DIFFERENCE"

    return {
        "mode": MODE,
        "research_only": True,
        "prospective_capture": True,
        "same_pit_input_snapshot": True,
        "legacy": legacy_view,
        "shared": shared_view,
        "direction_agreement": direction_agreement,
        "confidence_agreement": confidence_agreement,
        "full_thesis_agreement": direction_agreement and confidence_agreement,
        "divergence_reason": divergence,
        "memory_policy": {
            "legacy_memory_counted": _legacy_memory_counted(legacy or {}),
            "shared_memory_role": "EXPERIENCE_CONTEXT",
            "shared_memory_counts_as_independent_confirmation": False,
        },
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_brain_effect": "NONE",
        "decision_effect": "NONE",
        "execution_effect": "NONE",
        "capital_effect": "NONE",
        "historical_episode_rewrite": False,
        "promotion_eligible": False,
    }
