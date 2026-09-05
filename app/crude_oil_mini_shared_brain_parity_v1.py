from __future__ import annotations

from .crude_oil_mini_commodity_brain_shadow_v1 import (
    MODE as SHARED_MODE,
    synthesize_crude_shared_families,
)
from .crude_oil_mini_direction_brain_v2_integrated import MODE as LEGACY_MODE

MODE = "CRUDE_OIL_MINI_SHARED_BRAIN_PARITY_V1"
MEMORY_FAMILY = "DIRECTION_MEMORY"
REQUIRED_FAMILIES = (
    "LOCAL_STRUCTURE",
    "PARTICIPATION",
    "GLOBAL_CRUDE",
    "EVENT_REACTION",
    "DIRECTION_MEMORY",
)


def _unavailable_shared(reason: str) -> dict:
    return {
        "mode": SHARED_MODE,
        "status": "UNAVAILABLE",
        "reason": reason[:500],
        "research_only": True,
        "shadow_only": True,
        "same_pit_family_snapshot_as_legacy": True,
        "direction": "UNKNOWN",
        "direction_confidence": "UNKNOWN",
        "thesis_state": "PARITY_SYNTHESIS_UNAVAILABLE",
        "supporting_families": [],
        "opposing_families": [],
        "dependency_audit": {},
        "families": {},
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_brain_effect": "NONE",
        "decision_effect": "NONE",
        "execution_effect": "NONE",
        "capital_committed": 0,
        "promotion_eligible": False,
    }


def build_shared_shadow_from_legacy_families(legacy: dict) -> dict:
    """Run only shared synthesis over the exact legacy PIT family snapshot.

    No market source, candle store, option store, news source, or memory store is
    queried here. Any parity failure is converted to research-unavailable state so
    it cannot block or alter the already-frozen Current Mind decision.
    """
    families = dict((legacy or {}).get("families") or {})
    missing = [name for name in REQUIRED_FAMILIES if not isinstance(families.get(name), dict)]
    if missing:
        return _unavailable_shared(f"Legacy PIT family snapshot is incomplete: {','.join(missing)}")

    try:
        synthesis = synthesize_crude_shared_families(
            local=families["LOCAL_STRUCTURE"],
            participation=families["PARTICIPATION"],
            global_crude=families["GLOBAL_CRUDE"],
            event=families["EVENT_REACTION"],
            memory=families["DIRECTION_MEMORY"],
        )
    except Exception as exc:
        return _unavailable_shared(f"{exc.__class__.__name__}: {str(exc)}")

    thesis = synthesis["thesis"]
    normalized = synthesis["families"]
    return {
        "mode": SHARED_MODE,
        "status": "EVALUATED",
        "research_only": True,
        "shadow_only": True,
        "same_pit_family_snapshot_as_legacy": True,
        "direction": thesis["direction"],
        "direction_confidence": thesis["confidence"],
        "thesis_state": thesis["state"],
        "supporting_families": thesis["supporting_families"],
        "opposing_families": thesis["opposing_families"],
        "dependency_audit": thesis["dependency_audit"],
        "families": {row["family"]: row for row in normalized},
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_brain_effect": "NONE",
        "decision_effect": "NONE",
        "execution_effect": "NONE",
        "capital_committed": 0,
        "promotion_eligible": False,
    }


def _summary(result: dict, *, default_mode: str | None = None) -> dict:
    confidence = result.get("direction_confidence")
    if confidence in (None, ""):
        confidence = result.get("confidence")
    return {
        "mode": result.get("mode") or default_mode,
        "status": result.get("status", "EVALUATED"),
        "direction": str(result.get("direction") or "UNKNOWN").upper(),
        "confidence": str(confidence or "UNKNOWN").upper(),
        "thesis_state": result.get("thesis_state"),
        "supporting_families": list(result.get("supporting_families") or []),
        "opposing_families": list(result.get("opposing_families") or []),
    }


def _legacy_memory_counted(legacy: dict) -> bool:
    family = ((legacy.get("families") or {}).get(MEMORY_FAMILY) or {})
    if family.get("counts_for_direction") is not True:
        return False
    counted_names = set(legacy.get("supporting_families") or []) | set(legacy.get("opposing_families") or [])
    if MEMORY_FAMILY in counted_names:
        return True
    audit = legacy.get("dependency_audit") or {}
    counted = audit.get("counted") or []
    return any(row.get("family") == MEMORY_FAMILY for row in counted if isinstance(row, dict))


def build_shared_brain_parity(*, legacy: dict, shared: dict) -> dict:
    """Compare legacy and shared synthesis from one prospective PIT family snapshot."""
    legacy_view = _summary(legacy or {}, default_mode=LEGACY_MODE)
    shared_view = _summary(shared or {}, default_mode=SHARED_MODE)
    available = shared_view["status"] == "EVALUATED"
    direction_agreement = available and legacy_view["direction"] == shared_view["direction"]
    confidence_agreement = available and legacy_view["confidence"] == shared_view["confidence"]

    if not available:
        divergence = "SHARED_PARITY_UNAVAILABLE"
    elif direction_agreement and confidence_agreement:
        divergence = "NONE"
    elif _legacy_memory_counted(legacy or {}):
        divergence = "MEMORY_CONTEXT_ONLY_CORRECTION"
    elif direction_agreement:
        divergence = "CONFIDENCE_OR_DEPENDENCY_AUDIT_DIFFERENCE"
    else:
        divergence = "SHARED_CORE_CAUSAL_SYNTHESIS_DIFFERENCE"

    return {
        "mode": MODE,
        "status": "EVALUATED" if available else "UNAVAILABLE",
        "research_only": True,
        "prospective_capture": True,
        "same_pit_input_snapshot": True,
        "same_pit_family_snapshot": True,
        "legacy": legacy_view,
        "shared": shared_view,
        "direction_agreement": direction_agreement if available else None,
        "confidence_agreement": confidence_agreement if available else None,
        "full_thesis_agreement": (direction_agreement and confidence_agreement) if available else None,
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
