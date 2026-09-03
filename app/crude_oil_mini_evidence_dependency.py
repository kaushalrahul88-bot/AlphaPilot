from __future__ import annotations

DIRECTIONAL = {"BULLISH", "BEARISH"}


def audit_directional_independence(families: list[dict]) -> dict:
    """Suppress dependent and duplicate directional evidence before thesis counting."""
    counted: list[dict] = []
    suppressed: list[dict] = []
    origin_owner: dict[str, str] = {}
    duplicate_origins: dict[str, list[str]] = {}

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
        if origin in origin_owner:
            duplicate_origins.setdefault(origin, [origin_owner[origin]]).append(family)
            suppressed.append({
                "family": family,
                "stance": stance,
                "reason": "DUPLICATE_CAUSAL_ORIGIN",
                "independence_status": independence,
                "causal_origin": origin,
                "already_counted_family": origin_owner[origin],
            })
            continue

        origin_owner[origin] = family
        counted.append(row)

    return {
        "counted_families": [row.get("family") for row in counted],
        "counted_origins": sorted(origin_owner),
        "counted": counted,
        "suppressed": suppressed,
        "duplicate_origins": duplicate_origins,
    }
