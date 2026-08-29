from app.commodity_domain_knowledge import (
    COPPER_KNOWLEDGE_V1, MarketEvent, copper_knowledge_pack_v1,
    event_is_replay_eligible, surprise,
)


def test_copper_pack_is_research_only_and_has_provenance():
    pack=copper_knowledge_pack_v1()
    assert pack["research_only"] is True
    assert pack["production_rules_changed"] is False
    assert len(pack["items"]) >= 4
    for item in pack["items"]:
        assert item["source_name"]
        assert item["source_url"].startswith("https://")
        assert item["hypothesis_hook"]
        assert item["production_rule"] is False


def test_event_surprise_and_lookahead_guard():
    event=MarketEvent(
        event_id="x",commodity="COPPER",event_type="MACRO",
        published_at="2026-08-10T10:00:00+05:30",
        first_detected_at="2026-08-10T10:00:05+05:30",
        source_name="Official",source_url="https://example.invalid",
        source_tier="A_PRIMARY",headline="test",scheduled=True,
        expected_value=50.0,actual_value=48.0,
    )
    assert surprise(event) == -2.0
    assert event_is_replay_eligible(event,"2026-08-10T09:59:59+05:30") is False
    assert event_is_replay_eligible(event,"2026-08-10T10:00:05+05:30") is True


def test_no_knowledge_item_is_a_trade_rule():
    assert all(not item.production_rule for item in COPPER_KNOWLEDGE_V1)
