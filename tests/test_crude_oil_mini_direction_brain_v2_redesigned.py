from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta

from app.crude_oil_mini_direction_brain_v2_redesigned import (
    evaluate_redesigned_direction_brain_v2_shadow,
    integration_contract,
)

CLICK = "2026-09-03T15:30:00+05:30"


def _mini_candles(*, oi=False):
    rows = [
        ["2026-09-03T14:00:00+05:30", 100.0, 100.5, 99.5, 100.0, 100.0],
        ["2026-09-03T14:05:00+05:30", 100.0, 100.7, 99.8, 100.4, 110.0],
        ["2026-09-03T14:10:00+05:30", 100.4, 101.0, 100.2, 100.8, 120.0],
        ["2026-09-03T14:15:00+05:30", 100.8, 101.2, 100.6, 101.0, 130.0],
        ["2026-09-03T14:20:00+05:30", 101.0, 102.0, 100.9, 101.7, 220.0],
        ["2026-09-03T14:25:00+05:30", 101.7, 103.0, 101.5, 102.6, 320.0],
    ]
    if oi:
        for index, row in enumerate(rows):
            row.append(1000 + index * 25)
    return rows


def _snapshot():
    return {
        "structure": "UPTREND",
        "return_15m_pct": 0.25,
        "return_60m_pct": 0.40,
        "time_adjusted_relative_volume": 1.6,
        "session_vwap_gap_pct": 0.5,
    }


def _profile():
    return {"participation_confirming": 1.2}


def _benchmark_feed(offset=0.0):
    start = datetime.fromisoformat("2026-09-03T08:00:00+05:30")
    rows = []
    for index in range(7):
        base = 70.0 + offset + index * 0.5
        bar_start = start + timedelta(hours=index)
        rows.append({
            "bar_start": bar_start.isoformat(),
            "available_at": (bar_start + timedelta(hours=1)).isoformat(),
            "open": base,
            "high": base + 0.4,
            "low": base - 0.2,
            "close": base + 0.3,
            "volume": 1000.0 + index * 50.0,
        })
    return {"source": "fixture", "bar_minutes": 60, "data": rows}


def _global_probe():
    return {
        "feeds": {
            "WTI_CRUDE": _benchmark_feed(0.0),
            "BRENT_CRUDE": _benchmark_feed(5.0),
        }
    }


def _event(*, stance="BULLISH", material=True, novel=True):
    value = {
        "mechanism_stance": stance,
        "reaction": {"direction": stance, "confirmed": True},
    }
    if material:
        value["materiality_status"] = "MATERIAL"
    if novel:
        value["novelty_status"] = "NEW"
    return {
        "series": "CRUDE_NEWS",
        "event_id": "EVENT-1",
        "event_type": "SUPPLY_SHOCK",
        "observed_at": "2026-09-03T14:00:00+05:30",
        "available_at": "2026-09-03T14:00:00+05:30",
        "value": value,
    }


class CrudeOilMiniDirectionBrainV2RedesignedTests(unittest.TestCase):
    def test_local_plus_richer_global_crude_can_form_moderate_thesis(self):
        result = evaluate_redesigned_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot(),
            profile=_profile(),
            mini_candles=_mini_candles(oi=False),
            context_probe=_global_probe(),
        )
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["direction_confidence"], "MODERATE")
        self.assertEqual(result["supporting_families"], ["GLOBAL_CRUDE", "LOCAL_STRUCTURE"])
        participation = result["families"]["PARTICIPATION"]
        self.assertEqual(participation["state"], "PRICE_VOLUME_ONLY_DEPENDENT")
        self.assertFalse(participation["counts_for_direction"])
        self.assertEqual(result["families"]["GLOBAL_CRUDE"]["state"], "WTI_BRENT_STRUCTURE_MOMENTUM_CONFIRMED")

    def test_event_requires_materiality_novelty_and_confirmed_reaction(self):
        qualified = evaluate_redesigned_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot(),
            profile=_profile(),
            mini_candles=_mini_candles(oi=False),
            context_probe={"feeds": {}},
            event_records=[_event()],
        )
        self.assertEqual(qualified["direction"], "BULLISH")
        self.assertIn("EVENT_REACTION", qualified["supporting_families"])

        unqualified = evaluate_redesigned_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot(),
            profile=_profile(),
            mini_candles=_mini_candles(oi=False),
            context_probe={"feeds": {}},
            event_records=[_event(material=False)],
        )
        self.assertEqual(unqualified["direction"], "UNKNOWN")
        self.assertFalse(unqualified["families"]["EVENT_REACTION"]["counts_for_direction"])

    def test_opposing_independent_event_forces_conflicted_abstention(self):
        result = evaluate_redesigned_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot(),
            profile=_profile(),
            mini_candles=_mini_candles(oi=False),
            context_probe={"feeds": {}},
            event_records=[_event(stance="BEARISH")],
        )
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["direction_confidence"], "CONFLICTED")
        self.assertEqual(result["opposing_families"], ["EVENT_REACTION", "LOCAL_STRUCTURE"])

    def test_integration_is_shadow_only_and_has_no_obsolete_forward_protocol(self):
        contract = integration_contract()
        self.assertEqual(contract["current_mind_effect"], "NONE")
        self.assertEqual(contract["legacy_direction_v2_effect"], "NONE")
        self.assertFalse(contract["evaluation_protocol_defined_here"])
        self.assertFalse(contract["fixed_primary_horizon_defined_here"])
        self.assertFalse(contract["inspected_august_threshold_search_allowed"])
        self.assertTrue(contract["prospective_untouched_validation_required_for_promotion"])

        import app.crude_oil_mini_direction_brain_v2_redesigned as module
        source = inspect.getsource(module)
        self.assertNotIn("crude_oil_mini_current_mind", source)
        self.assertNotIn("crude_oil_mini_direction_forward", source)
        self.assertNotIn("phase_schedule", source)
        self.assertNotIn("2026-09-16", source)


if __name__ == "__main__":
    unittest.main()
