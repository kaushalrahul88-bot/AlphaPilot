from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta, timezone

from app import crude_oil_mini_direction_brain_v2_integrated as brain

IST = timezone(timedelta(hours=5, minutes=30))
CLICK = "2026-08-10T15:00:00+05:30"


def _snapshot(direction="BULLISH"):
    if direction == "BULLISH":
        return {
            "structure": "UPTREND",
            "return_15m_pct": 0.25,
            "return_60m_pct": 0.60,
            "time_adjusted_relative_volume": 1.5,
            "session_vwap_gap_pct": 0.4,
        }
    return {
        "structure": "DOWNTREND",
        "return_15m_pct": -0.25,
        "return_60m_pct": -0.60,
        "time_adjusted_relative_volume": 1.5,
        "session_vwap_gap_pct": -0.4,
    }


def _benchmark_feed(*, bullish=True):
    rows = []
    start = datetime(2026, 8, 10, 6, 0, tzinfo=IST)
    for index in range(8):
        level = 100.0 + index if bullish else 108.0 - index
        rows.append({
            "bar_start": (start + timedelta(hours=index)).isoformat(),
            "available_at": (start + timedelta(hours=index + 1)).isoformat(),
            "open": level,
            "high": level + 0.8,
            "low": level - 0.2,
            "close": level + 0.5,
            "volume": 1000.0 + index * 10,
        })
    return {
        "status": "AVAILABLE",
        "source": "unit-test completed hourly bars",
        "bar_minutes": 60,
        "data": rows,
    }


def _global_probe(*, bullish=True):
    return {
        "feeds": {
            "WTI_CRUDE": _benchmark_feed(bullish=bullish),
            "BRENT_CRUDE": _benchmark_feed(bullish=bullish),
        }
    }


def _initiative(direction="BULLISH"):
    return {
        "family": "PARTICIPATION",
        "causal_origin": "POSITIONING_FLOW",
        "independence_status": "INDEPENDENT",
        "depends_on": [],
        "counts_for_direction": True,
        "stance": direction,
        "state": "INITIATIVE_BUYING" if direction == "BULLISH" else "INITIATIVE_SELLING",
        "detail": {"fixture": True},
    }


def _event_record(direction="BULLISH"):
    return {
        "series": "CRUDE_NEWS",
        "event_id": "event-active",
        "event_type": "SUPPLY_SHOCK",
        "observed_at": "2026-08-10T12:00:00+05:30",
        "available_at": "2026-08-10T12:00:00+05:30",
        "source": "unit-test",
        "value": {
            "mechanism_stance": direction,
            "materiality_status": "MATERIAL",
            "novelty_status": "NEW",
            "reaction": {
                "direction": direction,
                "confirmed": True,
                "confirmation_sources": ["WTI_CRUDE", "BRENT_CRUDE"],
            },
        },
    }


class CrudeOilMiniDirectionBrainV2IntegratedTests(unittest.TestCase):
    def test_local_price_alone_cannot_manufacture_second_participation_vote(self):
        result = brain.evaluate_integrated_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot("BULLISH"),
            profile={"participation_confirming": 1.2},
            context_records=[],
            global_context_probe={},
            crude_candles=None,
            event_records=[],
            direction_memory_cases=[],
        )
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["direction_confidence"], "WEAK")
        self.assertEqual(result["supporting_families"], ["LOCAL_STRUCTURE"])
        self.assertFalse(result["families"]["PARTICIPATION"]["counts_for_direction"])

    def test_true_independent_participation_plus_local_can_form_moderate_thesis(self):
        result = brain.evaluate_integrated_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot("BULLISH"),
            profile={},
            context_records=[],
            global_context_probe={},
            event_records=[],
            direction_memory_cases=[],
            participation_observation=_initiative("BULLISH"),
        )
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["direction_confidence"], "MODERATE")
        self.assertEqual(
            set(result["dependency_audit"]["counted_origins"]),
            {"LOCAL_PRICE_STRUCTURE", "POSITIONING_FLOW"},
        )

    def test_rich_global_crude_can_confirm_local_without_old_one_hour_sign_fallback(self):
        result = brain.evaluate_integrated_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot("BULLISH"),
            profile={},
            context_records=[],
            global_context_probe=_global_probe(bullish=True),
            event_records=[],
            direction_memory_cases=[],
        )
        self.assertEqual(result["families"]["GLOBAL_CRUDE"]["stance"], "BULLISH")
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["direction_confidence"], "MODERATE")
        self.assertIn("GLOBAL_CRUDE", result["supporting_families"])

    def test_legacy_context_stance_does_not_replace_missing_global_market_history(self):
        old_style = [{
            "series": "WTI_CRUDE",
            "observed_at": "2026-08-10T13:00:00+05:30",
            "available_at": "2026-08-10T14:00:00+05:30",
            "source": "legacy",
            "value": {"stance": "BULLISH"},
        }, {
            "series": "BRENT_CRUDE",
            "observed_at": "2026-08-10T13:00:00+05:30",
            "available_at": "2026-08-10T14:00:00+05:30",
            "source": "legacy",
            "value": {"stance": "BULLISH"},
        }]
        result = brain.evaluate_integrated_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot("BULLISH"),
            profile={},
            context_records=old_style,
            global_context_probe={},
            event_records=[],
            direction_memory_cases=[],
        )
        self.assertEqual(result["families"]["GLOBAL_CRUDE"]["stance"], "UNKNOWN")
        self.assertEqual(result["direction"], "UNKNOWN")

    def test_event_lifecycle_can_confirm_local_but_declares_global_reaction_dependency(self):
        result = brain.evaluate_integrated_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot("BULLISH"),
            profile={},
            context_records=[],
            global_context_probe={},
            event_records=[_event_record("BULLISH")],
            direction_memory_cases=[],
        )
        event = result["families"]["EVENT_REACTION"]
        self.assertTrue(event["counts_for_direction"])
        self.assertEqual(event["stance"], "BULLISH")
        self.assertEqual(event["depends_on"], ["CROSS_MARKET_CRUDE"])
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["direction_confidence"], "MODERATE")

    def test_opposing_rich_global_crude_forces_conflicted_unknown(self):
        result = brain.evaluate_integrated_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot("BULLISH"),
            profile={},
            context_records=[],
            global_context_probe=_global_probe(bullish=False),
            event_records=[],
            direction_memory_cases=[],
        )
        self.assertEqual(result["families"]["GLOBAL_CRUDE"]["stance"], "BEARISH")
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["direction_confidence"], "CONFLICTED")

    def test_contract_does_not_smuggle_in_old_phase_or_fixed_horizon_protocol(self):
        contract = brain.integration_contract()
        self.assertFalse(contract["diagnostic_replay_protocol_frozen_here"])
        self.assertTrue(contract["diagnostic_replay_requires_separate_approval"])
        self.assertFalse(contract["prospective_schedule_defined_here"])
        self.assertFalse(contract["fixed_direction_horizon_defined_here"])
        self.assertFalse(contract["promotion_allowed"])
        source = inspect.getsource(brain)
        self.assertNotIn("crude_oil_mini_current_mind", source)
        self.assertNotIn("option_brain", source.lower().replace('"option_brain_action"', '').replace('"option_brain_effect"', ''))


if __name__ == "__main__":
    unittest.main()
