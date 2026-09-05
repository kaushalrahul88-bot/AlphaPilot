import unittest
from datetime import datetime, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_news_pit_enrichment import (
    CRYPTO_NEWS_ENRICHMENT_DATASET,
    CryptoNewsEnrichmentCapture,
    architecture_contract,
    enriched_news_evidence_from_pit_record,
    enriched_news_item_from_pit_record,
    news_enrichment_archive_record,
)


def _t(minute):
    return datetime(2026, 9, 5, 6, minute, tzinfo=timezone.utc)


def _capture(analysis_minute=35, *, version="NEWS_VERIFY_V1", direction="BULLISH"):
    return CryptoNewsEnrichmentCapture(
        event_key="BTC_ETF_FLOW_EVENT",
        assets=("BTC",),
        headline="Bitcoin ETF flow update",
        published_at=_t(25),
        raw_first_seen_at=_t(30),
        analysis_first_seen_at=_t(analysis_minute),
        source_name="Example Financial News",
        source_class="FINANCIAL_NEWS",
        source_tier="B_INDEPENDENT",
        verification_state="CONFIRMED",
        truth_confidence=0.90,
        market_impact_confidence=0.80,
        direction_hint=direction,
        materiality="HIGH",
        novelty="NEW",
        expected_horizon="intraday",
        input_news_ids=("news-1", "news-2"),
        input_source_keys=("source-1", "source-2"),
        analysis_method="NEWS_CORROBORATION_ENGINE",
        analysis_version=version,
        independent_corroboration_count=1,
        primary_source_confirmed=False,
        primary_source_ref="https://example.com/source",
        claim_summary="Confirmed material BTC flow update.",
    ).validated()


class CryptoNewsPitEnrichmentTests(unittest.TestCase):
    def test_analysis_cannot_be_backdated_before_raw_first_seen(self):
        with self.assertRaises(ValueError):
            CryptoNewsEnrichmentCapture(
                **{**_capture().__dict__, "analysis_first_seen_at": _t(29)}
            ).validated()

    def test_enrichment_is_separate_immutable_dataset(self):
        record = news_enrichment_archive_record(_capture())
        self.assertEqual(record.dataset, CRYPTO_NEWS_ENRICHMENT_DATASET)
        self.assertEqual(record.first_seen_at, _t(35))
        self.assertEqual(record.payload["raw_first_seen_at"], _t(30).isoformat())
        self.assertFalse(record.payload["raw_capture_rewritten"])
        self.assertFalse(record.payload["trade_generated"])

    def test_enrichment_is_not_visible_before_analysis_first_seen(self):
        row = news_enrichment_archive_record(_capture()).frozen_dict()
        with self.assertRaises(ValueError):
            enriched_news_evidence_from_pit_record(row, decision_at=_t(34), market_confirmation=True)

    def test_confirmed_enrichment_can_become_directional_only_after_analysis_and_market_confirmation(self):
        row = news_enrichment_archive_record(_capture()).frozen_dict()
        evidence = enriched_news_evidence_from_pit_record(row, decision_at=_t(36), market_confirmation=True)
        self.assertEqual(evidence.stance, "BULLISH")
        self.assertFalse(evidence.context_only)
        self.assertEqual(evidence.observed_at, _t(35))
        self.assertFalse(evidence.metadata["generates_instrument_trade"])

    def test_without_market_confirmation_enrichment_remains_context(self):
        row = news_enrichment_archive_record(_capture()).frozen_dict()
        evidence = enriched_news_evidence_from_pit_record(row, decision_at=_t(36), market_confirmation=False)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_item_preserves_raw_and_analysis_timestamps_separately(self):
        row = news_enrichment_archive_record(_capture()).frozen_dict()
        item = enriched_news_item_from_pit_record(row)
        self.assertEqual(item.first_seen_at, _t(35))
        self.assertEqual(item.metadata["raw_first_seen_at"], _t(30).isoformat())
        self.assertEqual(item.metadata["analysis_first_seen_at"], _t(35).isoformat())

    def test_later_analysis_revision_creates_new_record_instead_of_overwrite(self):
        ledger = ImmutableBtcPitLedger()
        first = news_enrichment_archive_record(_capture(35, version="NEWS_VERIFY_V1", direction="BULLISH"))
        second = news_enrichment_archive_record(_capture(40, version="NEWS_VERIFY_V2", direction="NEUTRAL"))
        ledger.insert_first_seen(first)
        ledger.insert_first_seen(second)
        rows = ledger.visible_as_of(_t(41), dataset=CRYPTO_NEWS_ENRICHMENT_DATASET)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["source_key"], rows[1]["source_key"])
        self.assertEqual(rows[0]["payload"]["direction_hint"], "BULLISH")
        self.assertEqual(rows[1]["payload"]["direction_hint"], "NEUTRAL")

    def test_contract_preserves_verification_latency_and_no_trade_generation(self):
        contract = architecture_contract()
        self.assertFalse(contract["raw_capture_rewritten"])
        self.assertTrue(contract["analysis_has_separate_first_seen"])
        self.assertFalse(contract["verification_can_be_backdated_to_raw_first_seen"])
        self.assertTrue(contract["later_analysis_creates_new_versioned_record"])
        self.assertTrue(contract["market_confirmation_still_required_at_decision"])
        self.assertFalse(contract["enrichment_itself_generates_trade"])


if __name__ == "__main__":
    unittest.main()
