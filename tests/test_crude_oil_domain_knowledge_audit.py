from app.crude_oil_domain_knowledge_audit import audit_crude_oil_domain_knowledge


def test_crude_domain_knowledge_audit_is_safe_and_ready():
    report = audit_crude_oil_domain_knowledge()
    assert report["ready_as_research_prior"] is True
    assert report["decision_effect"] == "NONE"
    assert report["unsafe_production_rule_ids"] == []
    assert report["missing_provenance_ids"] == []
    assert report["item_count"] >= 12
    assert report["families"]["inventories"] >= 1
    assert report["families"]["supply"] >= 1
    assert report["families"]["cross_market"] >= 1
