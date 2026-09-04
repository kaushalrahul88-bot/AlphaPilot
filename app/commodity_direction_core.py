from __future__ import annotations

from typing import Iterable

from .commodity_contract_continuity import retention_policy

DIRECTIONAL = {"BULLISH", "BEARISH"}
NON_CAUSAL_ROLES = {"MEMORY", "CONTEXT", "MODIFIER"}


def _norm(value, default="UNKNOWN") -> str:
    text = str(value or default).strip().upper()
    return text or default


def _norm_list(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values:
        text = _norm(value, "")
        if text and text not in out:
            out.append(text)
    return out


def normalize_family(
    row: dict,
    *,
    role: str,
    independence_status: str | None = None,
    depends_on_origins: Iterable[str] | None = None,
    independent_vote_registered: bool = False,
    force_context_only: bool = False,
) -> dict:
    """Normalize commodity-specific evidence into the shared Direction contract."""
    normalized = dict(row or {})
    family = _norm(normalized.get("family"), "UNKNOWN")
    origin = _norm(normalized.get("causal_origin"), "UNKNOWN")
    stance = _norm(normalized.get("stance"), "UNKNOWN")
    if stance not in DIRECTIONAL:
        stance = "UNKNOWN"

    normalized_role = _norm(role)
    wants_vote = bool(normalized.get("counts_for_direction")) and stance in DIRECTIONAL
    if force_context_only or (normalized_role in NON_CAUSAL_ROLES and not independent_vote_registered):
        wants_vote = False

    if independence_status is None:
        independence_status = "INDEPENDENT" if wants_vote else "CONTEXT_ONLY"

    normalized.update(
        {
            "family": family,
            "causal_origin": origin,
            "role": normalized_role,
            "stance": stance,
            "counts_for_direction": wants_vote,
            "independence_status": _norm(independence_status),
            "depends_on_origins": _norm_list(depends_on_origins if depends_on_origins is not None else normalized.get("depends_on_origins")),
            "independent_vote_registered": bool(independent_vote_registered),
        }
    )
    return normalized


def audit_directional_independence(families: list[dict]) -> dict:
    """Count distinct, auditable causal origins rather than nominal family labels."""
    accepted_by_origin: dict[str, dict] = {}
    suppressed: list[dict] = []

    for row in families:
        family = _norm(row.get("family"), "UNKNOWN")
        stance = _norm(row.get("stance"), "UNKNOWN")
        wants_vote = bool(row.get("counts_for_direction")) and stance in DIRECTIONAL
        if not wants_vote:
            continue

        role = _norm(row.get("role"), "UNKNOWN")
        independence = _norm(row.get("independence_status"), "UNKNOWN")
        origin = _norm(row.get("causal_origin"), "UNKNOWN")

        if role in NON_CAUSAL_ROLES and not bool(row.get("independent_vote_registered")):
            suppressed.append(
                {
                    "family": family,
                    "stance": stance,
                    "reason": "NON_CAUSAL_ROLE_NOT_REGISTERED_FOR_INDEPENDENT_VOTE",
                    "role": role,
                    "causal_origin": origin,
                }
            )
            continue
        if independence != "INDEPENDENT":
            suppressed.append(
                {
                    "family": family,
                    "stance": stance,
                    "reason": "FAMILY_NOT_INDEPENDENT",
                    "independence_status": independence,
                    "causal_origin": origin,
                }
            )
            continue
        if origin in {"", "UNKNOWN"}:
            suppressed.append(
                {
                    "family": family,
                    "stance": stance,
                    "reason": "CAUSAL_ORIGIN_UNDECLARED",
                    "causal_origin": origin,
                }
            )
            continue
        if origin in accepted_by_origin:
            suppressed.append(
                {
                    "family": family,
                    "stance": stance,
                    "reason": "DUPLICATE_CAUSAL_ORIGIN",
                    "causal_origin": origin,
                    "already_counted_family": accepted_by_origin[origin].get("family"),
                }
            )
            continue
        accepted_by_origin[origin] = row

    candidate_origins = set(accepted_by_origin)
    counted: list[dict] = []
    for origin, row in accepted_by_origin.items():
        dependencies = set(_norm_list(row.get("depends_on_origins")))
        overlapping = sorted((dependencies & candidate_origins) - {origin})
        if overlapping:
            suppressed.append(
                {
                    "family": row.get("family"),
                    "stance": row.get("stance"),
                    "reason": "DEPENDENT_ON_COUNTED_CAUSAL_ORIGIN",
                    "causal_origin": origin,
                    "depends_on_counted_origins": overlapping,
                }
            )
            continue
        counted.append(row)

    return {
        "counted_families": [row.get("family") for row in counted],
        "counted_origins": sorted(str(row.get("causal_origin")) for row in counted),
        "counted": counted,
        "suppressed": suppressed,
        "candidate_origins_before_dependency_suppression": sorted(candidate_origins),
    }


def build_direction_thesis(families: list[dict], *, minimum_confirmations: int = 2) -> dict:
    if minimum_confirmations < 2:
        raise ValueError("minimum_confirmations must be at least 2")

    dependency = audit_directional_independence(families)
    counted = dependency["counted"]
    bullish = [row for row in counted if row.get("stance") == "BULLISH"]
    bearish = [row for row in counted if row.get("stance") == "BEARISH"]

    if bullish and bearish:
        return {
            "direction": "UNKNOWN",
            "confidence": "CONFLICTED",
            "state": "INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION",
            "supporting_families": [],
            "opposing_families": sorted(str(row.get("family")) for row in counted),
            "dependency_audit": dependency,
        }

    supporting = bullish or bearish
    if len(supporting) < minimum_confirmations:
        return {
            "direction": "UNKNOWN",
            "confidence": "WEAK",
            "state": "INSUFFICIENT_INDEPENDENT_CONFIRMATION",
            "supporting_families": sorted(str(row.get("family")) for row in supporting),
            "opposing_families": [],
            "dependency_audit": dependency,
        }

    return {
        "direction": "BULLISH" if bullish else "BEARISH",
        "confidence": "STRONG" if len(supporting) >= 3 else "MODERATE",
        "state": "COHERENT_DIRECTION_THESIS",
        "supporting_families": sorted(str(row.get("family")) for row in supporting),
        "opposing_families": [],
        "dependency_audit": dependency,
    }


def architecture_contract() -> dict:
    return {
        "version": "COMMODITY_DIRECTION_CORE_V1",
        "shared_across_commodities": True,
        "commodity_specific_evidence_builders": True,
        "weighted_score_used": False,
        "minimum_independent_confirmations": 2,
        "one_vote_per_causal_origin": True,
        "dependency_deduplication": True,
        "memory_context_vote_by_default": False,
        "direction_implies_trade": False,
        "data_continuity": retention_policy(),
        "data_continuity_counts_as_direction_evidence": False,
        "research_only": True,
        "promotion_allowed": False,
    }
