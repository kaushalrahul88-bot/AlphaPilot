from __future__ import annotations

from collections import Counter

from .crude_oil_domain_knowledge import crude_oil_domain_knowledge_v1


def audit_crude_oil_domain_knowledge() -> dict:
    """Describe the Crude knowledge pack without changing Market-Brain behavior."""
    pack = crude_oil_domain_knowledge_v1()
    items = list(pack["items"])
    families = Counter(item["family"] for item in items)
    tiers = Counter(item["source_tier"] for item in items)
    statuses = Counter(item["status"] for item in items)
    unsafe = [item["id"] for item in items if item.get("production_rule")]
    missing_provenance = [
        item["id"]
        for item in items
        if not item.get("source_name") or not item.get("source_url") or not item.get("hypothesis_hook")
    ]
    return {
        "mode": "CRUDE_OIL_DOMAIN_KNOWLEDGE_AUDIT_V1",
        "version": pack["version"],
        "research_only": pack["research_only"],
        "production_rules_changed": pack["production_rules_changed"],
        "item_count": len(items),
        "families": dict(sorted(families.items())),
        "source_tiers": dict(sorted(tiers.items())),
        "statuses": dict(sorted(statuses.items())),
        "unsafe_production_rule_ids": unsafe,
        "missing_provenance_ids": missing_provenance,
        "guardrails": pack["guardrails"],
        "ready_as_research_prior": bool(
            items
            and pack["research_only"]
            and not pack["production_rules_changed"]
            and not unsafe
            and not missing_provenance
        ),
        "decision_effect": "NONE",
        "next_use": "Map only point-in-time observable Crude context to these hypotheses, then test incremental value versus the local-tape baseline.",
    }
