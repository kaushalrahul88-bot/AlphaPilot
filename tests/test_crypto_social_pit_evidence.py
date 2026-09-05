import unittest
from datetime import datetime, timezone

from app.crypto_social_pit_evidence import architecture_contract, social_evidence_from_pit_records
from app.crypto_social_pit_ingest import (
    ApprovedSocialCapture,
    SocialNarrativeEnrichment,
    social_enrichment_archive_record,
    social_raw_archive_record,
)


def _t(minute):
    return datetime(2026, 9, 5, 8, minute, tzinfo=timezone.utc)


def _raw():
    return social_raw_archive_record(ApprovedSocialCapture(
        provider="LICENSED_FEED",
        platform="SOCIAL_EXAMPLE",
        post_id="p-1",
        published_at=_t(0),
        first_seen_at=_t(1),
        source_url="https://example.invalid/p-1",
        text="Unverified BTC exchange claim",
        source_terms_ref="LICENSE_V1",
        access_approved=True,
        analysis_use_approved=True,
        retention_approved=True,
    ))


def _enrichment(raw, seen, *, event="event-1", velocity=0.95, truth=0.3, impact=0.9):
    return social_enrichment_archive_record(raw, SocialNarrativeEnrichment(
        raw_natural_key=raw.natural_key,
        analysis_first_seen_at=seen,
        event_key=event,
        assets=("BTC",),
        claim_summary="Possible exchange disruption",
        mention_velocity_percentile=velocity,
        source_historical_reliability=0.5,
        truth_confidence=truth,
        market_impact_confidence=impact,
        direction_hint="BEARISH",
        independent_source_count=1,
    ))


class CryptoSocialPitEvidenceTests(unittest.TestCase):
    def test_enrichment_is_invisible_before_analysis_first_seen(self):
        raw = _raw()
        enrichment = _enrichment(raw, _t(5))
        rows = [raw.frozen_dict(), enrichment.frozen_dict()]
        self.assertEqual(social_evidence_from_pit_records(rows, decision_at=_t(4)), [])
        evidence = social_evidence_from_pit_records(rows, decision_at=_t(5))
        self.assertEqual(len(evidence), 1)

    def test_social_evidence_remains_unknown_context_even_when_viral_and_high_impact(self):
        raw = _raw()
        enrichment = _enrichment(raw, _t(5), velocity=1.0, truth=0.95, impact=1.0)
        evidence = social_evidence_from_pit_records(
            [raw.frozen_dict(), enrichment.frozen_dict()],
            decision_at=_t(5),
        )[0]
        self.assertEqual(evidence.family, "CRYPTO_SOCIAL_NARRATIVE")
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertFalse(evidence.metadata["standalone_direction_allowed"])
        self.assertFalse(evidence.metadata["generates_instrument_trade"])

    def test_enrichment_without_visible_raw_record_is_rejected_from_evidence(self):
        raw = _raw()
        enrichment = _enrichment(raw, _t(5))
        self.assertEqual(
            social_evidence_from_pit_records([enrichment.frozen_dict()], decision_at=_t(5)),
            [],
        )

    def test_non_btc_social_enrichment_does_not_enter_btc_lane(self):
        raw = _raw()
        eth = social_enrichment_archive_record(raw, SocialNarrativeEnrichment(
            raw_natural_key=raw.natural_key,
            analysis_first_seen_at=_t(5),
            event_key="eth-event",
            assets=("ETH",),
            claim_summary="ETH-only narrative",
            mention_velocity_percentile=0.9,
            source_historical_reliability=0.5,
            truth_confidence=0.5,
            market_impact_confidence=0.8,
        ))
        self.assertEqual(
            social_evidence_from_pit_records([raw.frozen_dict(), eth.frozen_dict()], decision_at=_t(5)),
            [],
        )

    def test_latest_visible_revision_for_same_event_wins_without_future_leak(self):
        raw = _raw()
        first = _enrichment(raw, _t(5), velocity=0.4, truth=0.2, impact=0.5)
        later = _enrichment(raw, _t(7), velocity=0.99, truth=0.8, impact=0.95)
        rows = [raw.frozen_dict(), first.frozen_dict(), later.frozen_dict()]
        before = social_evidence_from_pit_records(rows, decision_at=_t(6))[0]
        after = social_evidence_from_pit_records(rows, decision_at=_t(7))[0]
        self.assertEqual(before.metadata["analysis_first_seen_at"], _t(5).isoformat())
        self.assertEqual(after.metadata["analysis_first_seen_at"], _t(7).isoformat())
        self.assertEqual(before.metadata["mention_velocity_percentile"], 0.4)
        self.assertEqual(after.metadata["mention_velocity_percentile"], 0.99)

    def test_contract_keeps_social_context_only(self):
        contract = architecture_contract()
        self.assertTrue(contract["raw_record_must_be_visible"])
        self.assertTrue(contract["enrichment_must_be_visible"])
        self.assertFalse(contract["future_enrichment_visible"])
        self.assertFalse(contract["social_direction_vote_allowed"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
