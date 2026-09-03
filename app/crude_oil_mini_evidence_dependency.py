from __future__ import annotations

DIRECTIONAL = {"BULLISH", "BEARISH"}


def audit_directional_independence(families: list[dict]) -> dict:
    """Suppress duplicate/dependent directional evidence before thesis formation.

    Directional confidence is based on unique primary causal origins, not the number
    of wrappers that happen to emit the same stance. Secondary confirmation
    dependencies are retained for audit but do not erase a genuinely exogenous
    primary causal origin.
    """
    counted: list[dict] = []
    suppressed: list[dict] = []
    origin_owner: dict[str, str] = {}
    duplicate_origins: dict[str, list[str]] = {}
    confirmation_dependencies: dict[str, list[str]] = {}

    for row in families or []:
        family = str(row.get("family") or "UNKNOWN")
        stance = str(row.get("stance") or "UNKNOWN").upper()
        wants_vote = bool(row.get("counts_for_direction")) and stance in DIRECTIONAL
        independence = str(row.get("independence_status") or "UNKNOWN").upper()
        origin = str(row.get("causal_origin") or "UNKNOWN").upper()
        dependencies = sorted({str(item).upper() for item in (row.get("depends_on") or []) if item})
        confirmation_dependencies[family] = dependencies

        if not wants_vote:
            continue
        if independence != "INDEPENDENT":
            suppressed.append({
                "family": family,
                "stance": stance,
                "reason": "FAMILY_NOT_INDEPENDENT",
                "independence_status": independence,
                "causal_origin": origin,
                "depends_on": dependencies,
            })
            continue
        if origin in {"", "UNKNOWN"}:
            suppressed.append({
                "family": family,
                "stance": stance,
                "reason": "CAUSAL_ORIGIN_UNDECLARED",
                "independence_status": independence,
                "causal_origin": origin,
                "depends_on": dependencies,
            })
            continue
        if origin in origin_owner:
            duplicate_origins.setdefault(origin, [origin_owner[origin]]).append(family)
            suppressed.append({
                "family": family,
                "stance": stance,
                "reason": "DUPLICATE_CAUSAL_ORIGIN",
                "independence_status": independence,
                "causal_origin": origin,
                "already_counted_family": origin_owner[origin],
                "depends_on": dependencies,
            })
            continue

        origin_owner[origin] = family
        counted.append(row)

    counted_origins = set(origin_owner)
    shared_confirmation = []
    for row in counted:
        family = str(row.get("family") or "UNKNOWN")
        overlaps = sorted(set(confirmation_dependencies.get(family, [])) & counted_origins)
        if overlaps:
            shared_confirmation.append({
                "family": family,
                "primary_causal_origin": str(row.get("causal_origin") or "UNKNOWN").upper(),
                "also_depends_on_counted_origins": overlaps,
                "effect": "AUDIT_ONLY_PRIMARY_ORIGIN_REMAINS_DISTINCT",
            })

    return {
        "counted_families": [row.get("family") for row in counted],
        "counted_origins": sorted(origin_owner),
        "counted": counted,
        "suppressed": suppressed,
        "duplicate_origins": duplicate_origins,
        "confirmation_dependencies": confirmation_dependencies,
        "shared_confirmation_dependencies": shared_confirmation,
        "policy": {
            "one_directional_vote_per_primary_causal_origin": True,
            "dependent_primary_evidence_cannot_vote": True,
            "secondary_confirmation_overlap_is_exposed_not_silently_double_counted": True,
        },
    }
