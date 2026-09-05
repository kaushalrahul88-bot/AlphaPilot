import unittest

from app.crypto_research_sources import source_class, source_inventory_v1


class CryptoResearchSourceTests(unittest.TestCase):
    def test_source_inventory_spans_online_offline_and_structured_sources(self):
        inventory = source_inventory_v1()
        mediums = {row["medium"] for row in inventory["sources"]}
        self.assertIn("BOOK", mediums)
        self.assertIn("ACADEMIC_PAPER", mediums)
        self.assertIn("NEWS", mediums)
        self.assertIn("NEWS_MAGAZINE", mediums)
        self.assertIn("SOCIAL_X", mediums)
        self.assertIn("REDDIT_FORUM", mediums)
        self.assertIn("VIDEO_PODCAST", mediums)
        self.assertIn("TELEGRAM_DISCORD", mediums)
        self.assertIn("ONCHAIN_DATA", mediums)
        self.assertIn("MARKET_DATA", mediums)

    def test_news_is_first_class_and_split_by_reporting_role(self):
        inventory = source_inventory_v1()
        ids = {row["id"] for row in inventory["sources"]}
        self.assertTrue(inventory["news_is_first_class_live_intelligence"])
        self.assertTrue(inventory["news_truth_and_market_impact_scored_separately"])
        self.assertTrue(inventory["news_primary_corroboration_preferred"])
        self.assertTrue({"BREAKING_NEWS", "FINANCIAL_NEWS", "CRYPTO_NATIVE_NEWS", "OFFICIAL_ANNOUNCEMENTS"}.issubset(ids))
        self.assertTrue(source_class("BREAKING_NEWS").timestamp_required)
        self.assertTrue(source_class("CRYPTO_NATIVE_NEWS").historical_reliability_tracking)

    def test_book_ingestion_does_not_default_to_full_text_persistence(self):
        books = source_class("BOOK_LIBRARY")
        self.assertEqual(books.ingestion_mode, "CLAIM_EXTRACTION")
        self.assertFalse(books.full_text_persistence_default)

    def test_social_sources_require_corroboration_and_track_reliability(self):
        social = source_class("X_SOCIAL")
        self.assertTrue(social.requires_corroboration)
        self.assertTrue(social.historical_reliability_tracking)
        self.assertFalse(source_inventory_v1()["community_source_standalone_trade_signal"])


if __name__ == "__main__":
    unittest.main()
