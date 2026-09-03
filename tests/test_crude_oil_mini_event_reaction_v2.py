from __future__ import annotations

import unittest

from app.crude_oil_mini_event_reaction_v2 import build_event_reaction_family_from_lifecycle


def _source(*, mechanism="BULLISH", materiality="MATERIAL", novelty="NEW", reaction="BULLISH"):
    return {
        "series": "CRUDE_NEWS",
        "event_id": "event-1",
        "event_type": "SUPPLY_DISRUPTION",
        "observed_at": "2026-08-10T12:00:00+05:30",
        "available_at": "2026-08-10T12:00:00+05:30",
        "value": {
            "mechanism_stance": mechanism,
            "materiality_status": materiality,
            "novelty_status": novelty,
            "reaction": {
                "direction": reaction,
                "confirmed": True,
                "confirmation_sources": ["WTI_CRUDE", "BRENT_CRUDE"],
            },
        },
    }


class CrudeOilMiniEventReactionV2Tests(unittest.TestCase):
    def test_fully_qualified_active_event_can_vote(self):
        source = _source()
        lifecycle = {
            "visible_event_count": 1,
            "active_context_count": 1,
            "events": [{
                "event_id": "event-1",
                "state": "REACTION_CONFIRMED_ACTIVE",
                "active_context": True,
                "terminal": False,
                "source_record": source,
            }],
            "active_events": [{"event_id": "event-1"}],
        }
        result = build_event_reaction_family_from_lifecycle(lifecycle)
        self.assertTrue(result["counts_for_direction"])
        self.assertEqual(result["stance"], "BULLISH")
        self.assertEqual(result["causal_origin"], "EXOGENOUS_INFORMATION")
        self.assertEqual(result["depends_on"], ["CROSS_MARKET_CRUDE"])

    def test_unassessed_materiality_cannot_vote_even_if_lifecycle_input_is_malformed(self):
        source = _source(materiality="UNASSESSED")
        lifecycle = {
            "visible_event_count": 1,
            "active_context_count": 1,
            "events": [{
                "event_id": "event-1",
                "state": "REACTION_CONFIRMED_ACTIVE",
                "active_context": True,
                "terminal": False,
                "source_record": source,
            }],
            "active_events": [{"event_id": "event-1"}],
        }
        result = build_event_reaction_family_from_lifecycle(lifecycle)
        self.assertFalse(result["counts_for_direction"])
        self.assertEqual(result["stance"], "UNKNOWN")

    def test_rejected_event_never_creates_reverse_vote(self):
        source = _source(mechanism="BULLISH", reaction="BEARISH")
        lifecycle = {
            "visible_event_count": 1,
            "active_context_count": 0,
            "events": [{
                "event_id": "event-1",
                "state": "REACTION_REJECTED",
                "active_context": False,
                "terminal": True,
                "source_record": source,
            }],
            "active_events": [],
        }
        result = build_event_reaction_family_from_lifecycle(lifecycle)
        self.assertFalse(result["counts_for_direction"])
        self.assertEqual(result["stance"], "UNKNOWN")
        self.assertEqual(result["state"], "BULLISH_EVENT_REJECTED")


if __name__ == "__main__":
    unittest.main()
