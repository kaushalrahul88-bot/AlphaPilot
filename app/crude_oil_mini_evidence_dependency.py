from __future__ import annotations

DIRECTIONAL = {"BULLISH", "BEARISH"}


def _norm_list(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out = []
    for value in values:
        text = str(value or "").strip().upper()
        if text and text not in out:
            out.append(text)
    return out


def audit_directional_independence(families: list[dict]) -> dict:
    """Count independent causal origins, not nominal family labels.

    Directional candidates must explicitly declare an independent causal origin. At
    most one candidate per origin is counted. A candidate is also suppressed when its
    declared confirmation/dependency origin is itself another directional candidate;
    this prevents, for example, an event confirmed by WTI/Brent from becoming a second
    vote beside GLOBAL_CRUDE.
    """
    accepted_by_origin: dict[str, dict] = {}
    suppressed: list[dict] = []

    for row in families:
        family = str(row.get("family") or "UNKNOWN")
        stance = str(row.get("stance") or "UNKNOWN").upper()
        wants_vote = bool(row.get("counts_for_direction")) and stance in DIRECTIONAL
        independence = str(row.get("independence_status") or "UNKNOWN").upper()
        origin = str(row.get("causal_origin") or "UNKNOWN").upper()
        if not wants_vote:
            continue
        if independence != "INDEPENDENT":
            suppressed.append({
                "family": family,
                "stance": stance,
                "reason": "FAMILY_NOT_INDEPENDENT",
                "independence_status": independence,
                "causal_origin": origin,
            })
            continue
        if origin in {"", "UNKNOWN"}:
            suppressed.append({
                "family": family,
                "stance": stance,
                "reason": "CAUSAL_ORIGIN_UNDECLARED",
                "independence_status": independence,
                "causal_origin": origin,
            })
            continue
        if origin in accepted_by_origin:
            suppressed.append({
                "family": family,
                "stance": stance,
                "reason": "DUPLICATE_CAUSAL_ORIGIN",
                "causal_origin": origin,
                "already_counted_family": accepted_by_origin[origin].get("family"),
            })
            continue
        accepted_by_origin[origin] = row

    candidate_origins = set(accepted_by_origin)
    counted = []
    for origin, row in accepted_by_origin.items():
        dependencies = set(_norm_list(row.get("depends_on_origins")))
        overlapping = sorted((dependencies & candidate_origins) - {origin})
        if overlapping:
            suppressed.append({
                "family": row.get("family"),
                "stance": row.get("stance"),
                "reason": "DEPENDENT_ON_COUNTED_CAUSAL_ORIGIN",
                "causal_origin": origin,
                "depends_on_counted_origins": overlapping,
            })
            continue
        counted.append(row)

    return {
        "counted_families": [row.get("family") for row in counted],
        "counted_origins": sorted(str(row.get("causal_origin")) for row in counted),
        "counted": counted,
        "suppressed": suppressed,
        "candidate_origins_before_dependency_suppression": sorted(candidate_origins),
    }


def dependency_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_DIRECTION_EVIDENCE_DEPENDENCY_V2",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "one_vote_per_causal_origin": True,
        "dependent_confirmation_can_double_count": False,
        "weighted_score_used": False,
        "promotion_allowed": False,
    }
