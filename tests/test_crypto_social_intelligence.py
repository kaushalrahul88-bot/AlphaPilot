import unittest
from datetime import datetime, timezone

from app.crypto_social_intelligence import (
    SocialNarrativeSignal,
    architecture_contract,
    deduplicate_social_signals,
    social_narrative_context,
)


def _t(hour=4, minute=0):
    return datetime(2026, 9, 5, hour, minute, tzinfo=timezone.utc)


def _signal(**overrides):
    values = {
        "signal_id": "s1",
        "event_key": "evt:btc:exchange-rumour",
        "assets": ("BTC",),
        "platform": "X",
        "first_seen_at": _t(3, 55),
        "source_tier": "D_COMMUNITY",
        "claim": "Potential material exchange development",
        "mention_velocity_percentile": 0.95,
        "source_historical_reliability": 0.60,
        "truth_confidence": 0.35,
        "market_impact_confidence": 0.90,
        "direction_hint": "BEARISH",
        "independent_source_count": 1,
        "primary_source_confirmed": False,
    }
    values.update(overrides)
    return SocialNarrativeSignal(**values)


class CryptoSocialIntelligenceTests(unittest.TestCase):
    def test_viral_low_truth_rumour_is_high_impact_context_not_direction(self):
        row = social_narrative_context(_signal(), decision_at=_t())
        self.assertEqual(row.stance, "UNKNOWN")
        self.assertTrue(row.context_only)
        self.assertIn("VIRAL_NARRATIVE", row.metadata["tags"])
        self.assertIn("LOW_TRUTH_CONFIDENCE", row.metadata["tags"])
        self.assertFalse(row.metadata["standalone_direction_allowed"])

    def test_high_confidence_corroborated_claim_is_only_news_verification_candidate(self):
        row = social_narrative_context(
            _signal(
                truth_confidence=0.9,
                source_historical_reliability=0.85,
                independent_source_count=3,
            ),
            decision_at=_t(),
        )
        self.assertTrue(row.metadata["promotion_candidate_for_news_verification"])
        self.assertEqual(row.stance, "UNKNOWN")
        self.assertTrue(row.context_only)

    def test_future_social_signal_is_not_visible_in_replay(self):
        with self.assertRaises(ValueError):
            social_narrative_context(_signal(first_seen_at=_t(4, 1)), decision_at=_t(4, 0))

    def test_duplicate_posts_do_not_manufacture_independent_events(self):
        first = _signal(signal_id="s1", platform="X", first_seen_at=_t(3, 50))
        second = _signal(signal_id="s2", platform="REDDIT", first_seen_at=_t(3, 51))
        result = deduplicate_social_signals([second, first])
        self.assertEqual(result["unique_event_count"], 1)
        self.assertEqual(result["report_count"], 2)
        self.assertEqual(result["event_platform_counts"]["evt:btc:exchange-rumour"], 2)
        self.assertEqual(result["canonical"][0].signal_id, "s1")

    def test_truth_and_market_impact_confidence_remain_separate(self):
        row = social_narrative_context(_signal(truth_confidence=0.2, market_impact_confidence=0.98), decision_at=_t())
        self.assertEqual(row.metadata["truth_confidence"], 0.2)
        self.assertEqual(row.metadata["market_impact_confidence"], 0.98)

    def test_architecture_contract_never_generates_trade(self):
        contract = architecture_contract()
        self.assertFalse(contract["viral_equals_true"])
        self.assertFalse(contract["standalone_direction_allowed"])
        self.assertTrue(contract["verified_fact_must_pass_news_intelligence"])
        self.assertFalse(contract["duplicate_posts_create_independent_votes"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
