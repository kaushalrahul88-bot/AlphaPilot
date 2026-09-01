import unittest

from app.crude_news_intelligence import assess_crude_news, apply_crude_news_intelligence
from app.crude_news_reaction_backtest import evaluate_crude_news_reactions


class CrudeNewsIntelligenceTests(unittest.TestCase):
    def _record(self, headline, **extra):
        return {
            "event_id": extra.pop("event_id", "E1"),
            "commodity": "CRUDEOIL",
            "available_at": extra.pop("available_at", "2026-08-18T20:48:24+05:30"),
            "source": "TEST",
            "value": {"headline": headline, **extra.pop("value", {})},
            **extra,
        }

    def test_hormuz_closure_is_bullish_causal_event(self):
        result = assess_crude_news(self._record("Iran says Strait of Hormuz remains shut after renewed attacks"))
        self.assertEqual(result["disposition"], "ALLOW")
        self.assertEqual(result["effect"], "BULLISH")
        self.assertEqual(result["transmission_mechanism"], "SUPPLY_ROUTE")

    def test_unconfirmed_hormuz_talks_do_not_vote(self):
        result = assess_crude_news(self._record("Iran and Oman hold talks on a proposal that could reopen Strait of Hormuz"))
        self.assertEqual(result["disposition"], "CONTEXT_ONLY")
        self.assertEqual(result["effect"], "UNKNOWN")

    def test_confirmed_hormuz_reopening_is_bearish_prior(self):
        result = assess_crude_news(self._record("Strait of Hormuz reopened and shipping resumed"))
        self.assertEqual(result["disposition"], "ALLOW")
        self.assertEqual(result["effect"], "BEARISH")

    def test_price_recap_cannot_become_independent_news_signal(self):
        result = assess_crude_news(self._record("Oil prices rise as traders react to Strait of Hormuz closure"))
        self.assertEqual(result["disposition"], "CONTEXT_ONLY")
        self.assertEqual(result["transmission_mechanism"], "PRICE_RECAP")

    def test_reconstructed_eia_consensus_fails_closed(self):
        record = self._record(
            "EIA crude inventories",
            event_type="EIA_CRUDE_INVENTORY",
            value={"actual_value": 4.405, "expected_value": 0.2, "expected_pit_safe": False},
        )
        result = assess_crude_news(record)
        self.assertEqual(result["disposition"], "CONTEXT_ONLY")
        self.assertEqual(result["effect"], "UNKNOWN")
        self.assertIn("EXPECTATION_NOT_PROVEN_AVAILABLE_BEFORE_RELEASE", result["reasons"])

    def test_pit_safe_eia_surprise_can_vote(self):
        bearish = assess_crude_news(self._record(
            "EIA crude inventories",
            event_type="EIA_CRUDE_INVENTORY",
            value={"actual_value": 4.0, "expected_value": 1.0, "expected_pit_safe": True},
        ))
        bullish = assess_crude_news(self._record(
            "EIA crude inventories",
            event_id="E2",
            event_type="EIA_CRUDE_INVENTORY",
            value={"actual_value": -4.0, "expected_value": -1.0, "expected_pit_safe": True},
        ))
        self.assertEqual((bearish["disposition"], bearish["effect"]), ("ALLOW", "BEARISH"))
        self.assertEqual((bullish["disposition"], bullish["effect"]), ("ALLOW", "BULLISH"))

    def test_duplicate_underlying_story_cannot_double_vote(self):
        first = self._record(
            "Strait of Hormuz remains shut after attack",
            event_id="A",
            underlying_event_id="H1",
            available_at="2026-08-18T10:00:00+05:30",
        )
        duplicate = self._record(
            "Strait of Hormuz remains shut after attack",
            event_id="B",
            underlying_event_id="H1",
            available_at="2026-08-18T10:10:00+05:30",
        )
        result = apply_crude_news_intelligence([first, duplicate])
        self.assertEqual(result["counts"]["ALLOW"], 1)
        self.assertEqual(result["counts"]["BLOCK"], 1)

    def test_reaction_backtest_anchors_after_event_not_same_bar(self):
        candles = [
            ["2026-08-18T20:45:00+05:30", 100, 101, 99, 100, 1],
            ["2026-08-18T20:50:00+05:30", 100, 101, 100, 101, 1],
            ["2026-08-18T20:55:00+05:30", 101, 103, 101, 102, 1],
            ["2026-08-18T21:00:00+05:30", 102, 104, 102, 103, 1],
            ["2026-08-18T21:05:00+05:30", 103, 105, 103, 104, 1],
        ]
        event = self._record(
            "Strait of Hormuz remains shut after attack",
            available_at="2026-08-18T20:50:00+05:30",
        )
        result = evaluate_crude_news_reactions(candles, [event], horizons=(15,))
        self.assertEqual(result["scored_directional_events"], 1)
        scored = result["events"][0]
        self.assertEqual(scored["reaction"]["entry_bar_start"], "2026-08-18T20:55:00+05:30")
        self.assertTrue(scored["reaction"]["horizons"]["15"]["direction_aligned"])


if __name__ == "__main__":
    unittest.main()
