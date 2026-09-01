from app.crude_oil_domain_knowledge import CRUDE_OIL_KNOWLEDGE_V1, crude_oil_domain_knowledge_v1


def test_crude_domain_knowledge_is_research_only_and_non_trading():
    pack = crude_oil_domain_knowledge_v1()
    assert pack["version"] == "CRUDE_OIL_DOMAIN_KNOWLEDGE_V1"
    assert pack["research_only"] is True
    assert pack["production_rules_changed"] is False
    assert pack["guardrails"]["knowledge_cannot_create_orders"] is True
    assert pack["guardrails"]["historical_news_requires_first_detected_at"] is True
    assert pack["guardrails"]["unknown_context_stays_unknown"] is True
    assert pack["guardrails"]["no_copper_threshold_transfer"] is True
    assert pack["guardrails"]["no_regular_crude_substitution_for_crude_oil_mini"] is True
    assert all(item.production_rule is False for item in CRUDE_OIL_KNOWLEDGE_V1)


def test_crude_domain_knowledge_has_required_crude_specific_families():
    families = {item.family for item in CRUDE_OIL_KNOWLEDGE_V1}
    required = {
        "cross_market",
        "fundamentals",
        "inventories",
        "refining",
        "supply",
        "geopolitics",
        "weather",
        "term_structure",
        "demand",
        "options_volatility",
    }
    assert required <= families


def test_crude_domain_items_have_provenance_conditions_and_exceptions():
    assert len(CRUDE_OIL_KNOWLEDGE_V1) >= 12
    ids = [item.id for item in CRUDE_OIL_KNOWLEDGE_V1]
    assert len(ids) == len(set(ids))
    for item in CRUDE_OIL_KNOWLEDGE_V1:
        assert item.commodity == "CRUDE_OIL"
        assert item.source_name
        assert item.source_url.startswith("https://")
        assert item.source_tier in {"A_PRIMARY", "B_RESEARCH", "C_PROFESSIONAL", "D_PRACTITIONER", "E_DISCOVERY"}
        assert item.status in {"ESTABLISHED_CONTEXT", "HYPOTHESIS_ONLY"}
        assert item.conditions
        assert item.exceptions
        assert item.hypothesis_hook
        assert item.option_implication


def test_knowledge_pack_does_not_embed_historical_event_outcomes():
    forbidden_keys = {"published_at", "first_detected_at", "actual_value", "expected_value", "result", "realized_r"}
    for item in crude_oil_domain_knowledge_v1()["items"]:
        assert forbidden_keys.isdisjoint(item)
