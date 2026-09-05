import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_news_capture_scheduler import (
    CryptoNewsCapturePolicy,
    CryptoNewsPitCaptureScheduler,
    architecture_contract,
)
from app.crypto_news_pit_capture import CRYPTO_NEWS_DATASET
from app.newsapi_crypto_provider import NewsApiRawArticleCapture


def _t(minute=30):
    return datetime(2026, 9, 5, 6, minute, tzinfo=timezone.utc)


def _article(first_seen_at, *, description="A market development."):
    return NewsApiRawArticleCapture(
        first_seen_at=first_seen_at,
        published_at=datetime(2026, 9, 5, 6, 25, tzinfo=timezone.utc),
        source_id="example",
        source_name="Example News",
        author="Reporter",
        title="Bitcoin market update",
        description=description,
        url="https://example.com/story",
        content_excerpt="Short API excerpt",
    ).validated()


class _Provider:
    def __init__(self, factory=None):
        self.calls = 0
        self.factory = factory or (lambda seen: [_article(seen)])

    def capture_latest(self, *, first_seen_at):
        self.calls += 1
        return self.factory(first_seen_at)


class _FailingProvider:
    def __init__(self):
        self.calls = 0

    def capture_latest(self, *, first_seen_at):
        self.calls += 1
        raise RuntimeError("news provider unavailable")


class CryptoNewsCaptureSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_scheduler_makes_no_provider_or_store_call(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = CryptoNewsPitCaptureScheduler(provider=provider, store=ledger)
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "CRYPTO_NEWS_CAPTURE_DISABLED")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(ledger.manifest()["record_count"], 0)
        self.assertFalse(result["trade_generated"])

    async def test_enabled_scheduler_archives_raw_news_without_direction(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = CryptoNewsPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=CryptoNewsCapturePolicy(enabled=True, poll_seconds=60),
        )
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "CRYPTO_NEWS_CAPTURE_CYCLE_COMPLETE")
        self.assertEqual(result["captured"][0]["dataset"], CRYPTO_NEWS_DATASET)
        self.assertEqual(result["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertFalse(result["captured"][0]["direction_assigned"])
        self.assertFalse(result["captured"][0]["truth_confidence_assigned"])
        self.assertFalse(result["trade_generated"])

    async def test_same_article_seen_later_is_idempotent_and_preserves_first_seen(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = CryptoNewsPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=CryptoNewsCapturePolicy(enabled=True),
        )
        first = await scheduler.run_cycle(now=_t())
        second = await scheduler.run_cycle(now=_t() + timedelta(minutes=1))
        self.assertEqual(first["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(second["captured"][0]["storage_status"], "IDEMPOTENT_DUPLICATE")
        rows = ledger.visible_as_of(_t() + timedelta(hours=1), dataset=CRYPTO_NEWS_DATASET)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["first_seen_at"], _t().isoformat())

    async def test_changed_later_article_payload_does_not_overwrite_first_seen(self):
        calls = {"count": 0}
        def factory(seen):
            calls["count"] += 1
            description = "Original" if calls["count"] == 1 else "Changed later"
            return [_article(seen, description=description)]

        ledger = ImmutableBtcPitLedger()
        scheduler = CryptoNewsPitCaptureScheduler(
            provider=_Provider(factory),
            store=ledger,
            policy=CryptoNewsCapturePolicy(enabled=True),
        )
        first = await scheduler.run_cycle(now=_t())
        second = await scheduler.run_cycle(now=_t() + timedelta(minutes=1))
        self.assertEqual(first["status"], "CRYPTO_NEWS_CAPTURE_CYCLE_COMPLETE")
        self.assertEqual(second["status"], "CRYPTO_NEWS_CAPTURE_CYCLE_PARTIAL_FAILURE")
        self.assertTrue(second["errors"][0]["first_seen_record_preserved"])
        rows = ledger.visible_as_of(_t() + timedelta(hours=1), dataset=CRYPTO_NEWS_DATASET)
        self.assertEqual(rows[0]["payload"]["description"], "Original")

    async def test_provider_failure_is_explicit_and_never_trade(self):
        provider = _FailingProvider()
        ledger = ImmutableBtcPitLedger()
        scheduler = CryptoNewsPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=CryptoNewsCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "CRYPTO_NEWS_CAPTURE_PROVIDER_FAILURE")
        self.assertEqual(ledger.manifest()["record_count"], 0)
        self.assertFalse(result["trade_generated"])

    def test_policy_and_contract_are_fail_closed(self):
        with self.assertRaises(ValueError):
            CryptoNewsCapturePolicy(enabled=True, poll_seconds=29).validated()
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertFalse(contract["scheduler_starts_at_import"])
        self.assertTrue(contract["raw_capture_only"])
        self.assertFalse(contract["news_feed_assigns_direction"])
        self.assertFalse(contract["news_feed_assigns_truth_confidence"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
