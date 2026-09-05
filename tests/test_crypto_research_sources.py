from app.crypto_research_sources import source_class, source_inventory_v1


def test_source_inventory_spans_online_offline_and_structured_sources():
    inventory = source_inventory_v1()
    mediums = {row["medium"] for row in inventory["sources"]}
    assert "BOOK" in mediums
    assert "ACADEMIC_PAPER" in mediums
    assert "NEWS_MAGAZINE" in mediums
    assert "SOCIAL_X" in mediums
    assert "REDDIT_FORUM" in mediums
    assert "VIDEO_PODCAST" in mediums
    assert "TELEGRAM_DISCORD" in mediums
    assert "ONCHAIN_DATA" in mediums
    assert "MARKET_DATA" in mediums


def test_book_ingestion_does_not_default_to_full_text_persistence():
    books = source_class("BOOK_LIBRARY")
    assert books.ingestion_mode == "CLAIM_EXTRACTION"
    assert books.full_text_persistence_default is False


def test_social_sources_require_corroboration_and_track_reliability():
    social = source_class("X_SOCIAL")
    assert social.requires_corroboration is True
    assert social.historical_reliability_tracking is True
    assert source_inventory_v1()["community_source_standalone_trade_signal"] is False
