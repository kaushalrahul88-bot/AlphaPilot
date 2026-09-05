import unittest
from datetime import datetime, timezone

from app.crypto_news_intelligence import news_event_context
from app.crypto_news_pit_capture import (
    CRYPTO_NEWS_DATASET,
    architecture_contract,
    newsapi_article_archive_record,
    raw_news_item_from_pit_record,
)
from app.newsapi_crypto_provider import NewsApiRawArticleCapture


def _t():
    return datetime(2026, 9, 5, 6, 30, tzinfo=timezone.utc)


def _capture():
    return NewsApiRawArticleCapture(
        first_seen_at=_t(),
        published_at=datetime(2026, 9, 5, 6, 25, tzinfo=timezone.utc),
        source_id="example",
        source_name="Example News",
        author="Reporter",
        title="Bitcoin market update",
        description="A market development.",
        url="https://example.com/story",
        content_excerpt="Short API excerpt",
    ).validated()


class CryptoNewsPitCaptureTests(unittest.TestCase):
    def test_raw_article_archives_without_direction_or_truth_assignment(self):
        record = newsapi_article_archive_record(_capture())
        self.assertEqual(record.dataset, CRYPTO_NEWS_DATASET)
        self.assertEqual(record.event_at.isoformat(), "2026-09-05T06:25:00+00:00")
        self.assertEqual(record.first_seen_at, _t())
        self.assertFalse(record.payload["truth_confidence_assigned"])
        self.assertFalse(record.payload["direction_assigned"])
        self.assertFalse(record.payload["provider_result_treated_as_confirmed_fact"])

    def test_raw_pit_record_converts_to_unverified_context_only_news_item(self):
        record = newsapi_article_archive_record(_capture()).frozen_dict()
        item = raw_news_item_from_pit_record(record, assets=("BTC",))
        self.assertEqual(item.source_tier, "E_UNVERIFIED")
        self.assertEqual(item.verification_state, "UNVERIFIED")
        self.assertEqual(item.direction_hint, "UNKNOWN")
        evidence = news_event_context(item, decision_at=_t(), market_confirmation=True)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertFalse(evidence.metadata["generates_instrument_trade"])

    def test_news_is_invisible_before_alphapilot_first_seen_even_if_already_published(self):
        record = newsapi_article_archive_record(_capture()).frozen_dict()
        item = raw_news_item_from_pit_record(record)
        before_seen = datetime(2026, 9, 5, 6, 29, 59, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            news_event_context(item, decision_at=before_seen)

    def test_contract_prevents_retroactive_classifier_rewrite(self):
        contract = architecture_contract()
        self.assertTrue(contract["raw_article_first_seen_is_immutable"])
        self.assertTrue(contract["published_at_separate_from_first_seen_at"])
        self.assertFalse(contract["later_classifier_may_rewrite_raw_capture"])
        self.assertFalse(contract["raw_provider_article_is_confirmed_fact"])
        self.assertFalse(contract["raw_provider_article_has_direction"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
