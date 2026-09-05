import unittest
from datetime import datetime, timezone

from app.crypto_news_intelligence import (
    CryptoNewsItem,
    architecture_contract,
    deduplicate_news,
    news_event_context,
    news_replay_eligible,
)


def _t(hour: int = 4, minute: int = 0):
    return datetime(2026, 9, 5, hour, minute, tzinfo=timezone.utc)


def _confirmed(**overrides):
    values = {
        "news_id": "n1",
        "event_key": "evt:btc:regulatory",
        "assets": ("BTC",),
        "headline": "Confirmed material crypto development",
        "published_at": _t(3, 50),
        "first_seen_at": _t(3, 51),
        "source_name": "Professional News",
        "source_class": "BREAKING_NEWS",
        "source_tier": "C_PROFESSIONAL",
        "verification_state": "CONFIRMED",
        "truth_confidence": 0.95,
        "market_impact_confidence": 0.85,
        "direction_hint": "BULLISH",
        "materiality": "HIGH",
        "novelty": "NEW",
        "expected_horizon": "intraday",
    }
    values.update(overrides)
    return CryptoNewsItem(**values)


class CryptoNewsIntelligenceTests(unittest.TestCase):
    def test_replay_uses_first_seen_not_hindsight(self):
        item = _confirmed(first_seen_at=_t(4, 5))
        self.assertFalse(news_replay_eligible(item, decision_at=_t(4, 4)))
        self.assertTrue(news_replay_eligible(item, decision_at=_t(4, 5)))
        with self.assertRaises(ValueError):
            news_event_context(
                item,
                decision_at=_t(4, 4),
                independent_corroboration_count=2,
                market_confirmation=True,
            )

    def test_confirmed_corroborated_market_confirmed_news_can_be_one_directional_origin(self):
        evidence = news_event_context(
            _confirmed(),
            decision_at=_t(4, 0),
            independent_corroboration_count=2,
            market_confirmation=True,
        )
        self.assertEqual(evidence.stance, "BULLISH")
        self.assertFalse(evidence.context_only)
        self.assertEqual(evidence.causal_origin, "EVENT_INFORMATION")
        self.assertFalse(evidence.metadata["generates_instrument_trade"])

    def test_unverified_rumour_can_have_high_impact_but_remains_context_only(self):
        item = _confirmed(
            source_name="Anonymous Social Account",
            source_class="X_SOCIAL",
            source_tier="E_UNVERIFIED",
            verification_state="UNVERIFIED",
            truth_confidence=0.25,
            market_impact_confidence=0.95,
        )
        evidence = news_event_context(
            item,
            decision_at=_t(4, 0),
            independent_corroboration_count=5,
            market_confirmation=True,
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertEqual(evidence.metadata["truth_confidence"], 0.25)
        self.assertEqual(evidence.metadata["market_impact_confidence"], 0.95)

    def test_confirmed_professional_news_still_requires_corroboration_and_market_confirmation(self):
        no_corroboration = news_event_context(_confirmed(), decision_at=_t(4, 0), market_confirmation=True)
        no_market_confirmation = news_event_context(
            _confirmed(),
            decision_at=_t(4, 0),
            independent_corroboration_count=2,
            market_confirmation=False,
        )
        self.assertTrue(no_corroboration.context_only)
        self.assertTrue(no_market_confirmation.context_only)

    def test_primary_confirmed_news_can_satisfy_corroboration_gate_but_not_market_gate(self):
        item = _confirmed(source_name="Regulator", source_class="OFFICIAL_ANNOUNCEMENTS", source_tier="A_PRIMARY")
        admitted = news_event_context(item, decision_at=_t(4, 0), primary_source_confirmed=True, market_confirmation=True)
        not_market_confirmed = news_event_context(item, decision_at=_t(4, 0), primary_source_confirmed=True, market_confirmation=False)
        self.assertFalse(admitted.context_only)
        self.assertTrue(not_market_confirmed.context_only)

    def test_duplicate_reports_do_not_manufacture_independent_events(self):
        first = _confirmed(news_id="n1", first_seen_at=_t(3, 51))
        second = _confirmed(news_id="n2", first_seen_at=_t(3, 52), source_name="Second Outlet")
        result = deduplicate_news([second, first])
        self.assertEqual(result["unique_event_count"], 1)
        self.assertEqual(result["report_count"], 2)
        self.assertEqual(result["canonical"][0]["news_id"], "n1")

    def test_first_seen_cannot_precede_publication(self):
        item = _confirmed(published_at=_t(4, 0), first_seen_at=_t(3, 59))
        with self.assertRaises(ValueError):
            item.normalized()

    def test_news_architecture_never_generates_options_or_futures_trade(self):
        contract = architecture_contract()
        self.assertTrue(contract["news_first_class_live_intelligence"])
        self.assertTrue(contract["truth_confidence_separate_from_market_impact_confidence"])
        self.assertFalse(contract["headline_is_trade_signal"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
