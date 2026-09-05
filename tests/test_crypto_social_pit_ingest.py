import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_social_pit_ingest import (
    ApprovedSocialCapture,
    SOCIAL_ENRICHMENT_DATASET,
    SOCIAL_RAW_DATASET,
    SocialNarrativeEnrichment,
    architecture_contract,
    social_enrichment_archive_record,
    social_raw_archive_record,
)


def _t(minute=0):
    return datetime(2026, 9, 5, 7, minute, tzinfo=timezone.utc)


def _raw(**overrides):
    values = {
        "provider": "LICENSED_FEED",
        "platform": "SOCIAL_EXAMPLE",
        "post_id": "post-123",
        "published_at": _t(0),
        "first_seen_at": _t(2),
        "source_url": "https://example.invalid/post-123",
        "author_ref": "public-author-ref",
        "text": "Unverified claim about a large crypto event.",
        "engagement": {"likes": 10, "replies": 2},
        "source_terms_ref": "LICENSE_CONTRACT_2026_09",
        "access_approved": True,
        "analysis_use_approved": True,
        "retention_approved": True,
        "source_version": "LICENSED_FEED_V1",
    }
    values.update(overrides)
    return ApprovedSocialCapture(**values)


class CryptoSocialPitIngestTests(unittest.TestCase):
    def test_raw_capture_requires_verified_rights(self):
        for field in ("access_approved", "analysis_use_approved", "retention_approved"):
            with self.subTest(field=field):
                capture = _raw(**{field: False})
                with self.assertRaises(ValueError):
                    social_raw_archive_record(capture)

    def test_public_visibility_is_not_enough_without_terms_reference(self):
        with self.assertRaises(ValueError):
            social_raw_archive_record(_raw(source_terms_ref=""))

    def test_raw_capture_archives_first_seen_without_truth_or_direction(self):
        record = social_raw_archive_record(_raw())
        self.assertEqual(record.dataset, SOCIAL_RAW_DATASET)
        self.assertEqual(record.event_at, _t(0))
        self.assertEqual(record.first_seen_at, _t(2))
        self.assertFalse(record.payload["truth_assigned"])
        self.assertFalse(record.payload["direction_assigned"])
        self.assertFalse(record.payload["virality_equals_truth"])
        self.assertFalse(record.payload["engagement_equals_market_impact"])
        self.assertFalse(record.payload["generates_instrument_trade"])

    def test_raw_post_is_invisible_before_alphapilot_first_seen(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(social_raw_archive_record(_raw()))
        self.assertEqual(ledger.visible_as_of(_t(1), dataset=SOCIAL_RAW_DATASET), [])
        self.assertEqual(len(ledger.visible_as_of(_t(2), dataset=SOCIAL_RAW_DATASET)), 1)

    def test_first_seen_cannot_precede_published_and_edit_cannot_precede_publish(self):
        with self.assertRaises(ValueError):
            social_raw_archive_record(_raw(first_seen_at=_t(0) - timedelta(seconds=1)))
        with self.assertRaises(ValueError):
            social_raw_archive_record(_raw(edited_at=_t(0) - timedelta(seconds=1)))

    def test_deleted_capture_may_preserve_tombstone_without_text(self):
        record = social_raw_archive_record(_raw(deleted=True, text=None, edited_at=_t(3)))
        self.assertTrue(record.payload["deleted"])
        self.assertIsNone(record.payload["text"])
        self.assertEqual(record.payload["edited_at"], _t(3).isoformat())

    def test_enrichment_has_its_own_analysis_first_seen_and_cannot_backdate(self):
        raw = social_raw_archive_record(_raw())
        enrichment = SocialNarrativeEnrichment(
            raw_natural_key=raw.natural_key,
            analysis_first_seen_at=_t(5),
            event_key="event-abc",
            assets=("BTC",),
            claim_summary="Possible exchange disruption claim.",
            mention_velocity_percentile=0.95,
            source_historical_reliability=0.40,
            truth_confidence=0.30,
            market_impact_confidence=0.85,
            direction_hint="BEARISH",
            independent_source_count=1,
        )
        record = social_enrichment_archive_record(raw, enrichment)
        self.assertEqual(record.dataset, SOCIAL_ENRICHMENT_DATASET)
        self.assertEqual(record.first_seen_at, _t(5))
        self.assertEqual(record.payload["raw_first_seen_at"], _t(2).isoformat())
        self.assertFalse(record.payload["standalone_direction_allowed"])
        self.assertTrue(record.payload["verified_fact_must_pass_news_intelligence"])
        self.assertFalse(record.payload["generates_instrument_trade"])

        backdated = SocialNarrativeEnrichment(
            raw_natural_key=raw.natural_key,
            analysis_first_seen_at=_t(1),
            event_key="event-abc",
            assets=("BTC",),
            claim_summary="Backdated analysis.",
            mention_velocity_percentile=0.5,
            source_historical_reliability=0.5,
            truth_confidence=0.5,
            market_impact_confidence=0.5,
        )
        with self.assertRaises(ValueError):
            social_enrichment_archive_record(raw, backdated)

    def test_enrichment_visibility_uses_analysis_time_not_raw_post_time(self):
        raw = social_raw_archive_record(_raw())
        enrichment = social_enrichment_archive_record(
            raw,
            SocialNarrativeEnrichment(
                raw_natural_key=raw.natural_key,
                analysis_first_seen_at=_t(5),
                event_key="event-abc",
                assets=("BTC",),
                claim_summary="Narrative cluster analysis.",
                mention_velocity_percentile=0.95,
                source_historical_reliability=0.6,
                truth_confidence=0.4,
                market_impact_confidence=0.8,
            ),
        )
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(raw)
        ledger.insert_first_seen(enrichment)
        self.assertEqual(ledger.visible_as_of(_t(4), dataset=SOCIAL_ENRICHMENT_DATASET), [])
        self.assertEqual(len(ledger.visible_as_of(_t(5), dataset=SOCIAL_ENRICHMENT_DATASET)), 1)

    def test_contract_forbids_scraping_and_trade_generation(self):
        contract = architecture_contract()
        self.assertFalse(contract["public_visibility_equals_permission"])
        self.assertTrue(contract["approved_access_required"])
        self.assertTrue(contract["approved_analysis_use_required"])
        self.assertTrue(contract["approved_retention_required"])
        self.assertFalse(contract["scraping_implemented"])
        self.assertFalse(contract["reddit_provider_implemented"])
        self.assertFalse(contract["x_provider_implemented"])
        self.assertFalse(contract["standalone_direction_allowed"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
