from __future__ import annotations

import inspect
import unittest
from datetime import date, timedelta

from app import crude_oil_mini_direction_forward as forward
from app.commodity_time import parse_ist_timestamp


def _snapshot(click_timestamp: str) -> dict:
    click = parse_ist_timestamp(click_timestamp)
    return {
        "timestamp": (click - timedelta(minutes=5)).isoformat(),
        "price": 100.0,
        "structure": "UPTREND",
        "return_15m_pct": 0.25,
        "return_60m_pct": 0.40,
        "time_adjusted_relative_volume": 2.0,
    }


def _profile() -> dict:
    return {"participation_confirming": 1.0}


def _future_candles(click_timestamp: str):
    click = parse_ist_timestamp(click_timestamp)
    rows = []
    for minutes, close in ((15, 100.2), (30, 100.4), (60, 100.8), (120, 101.0)):
        start = click + timedelta(minutes=minutes - 5)
        rows.append([start.isoformat(), 100.0, close + 0.1, 99.9, close, 10.0, None])
    return rows


class CrudeOilMiniDirectionForwardTests(unittest.TestCase):
    def test_phase_one_schedule_is_clock_only_and_frozen(self):
        days = forward.validation_days()
        self.assertEqual(days[0], date(2026, 9, 3))
        self.assertEqual(days[-1], date(2026, 9, 16))
        self.assertEqual(len(days), 10)
        schedule = forward.phase_schedule()
        self.assertEqual(len(schedule), 200)
        self.assertEqual(schedule, forward.phase_schedule())
        per_day = {}
        for row in schedule:
            per_day[row["session"]] = per_day.get(row["session"], 0) + 1
            self.assertIn("CLOCK_ONLY", row["sampling"])
        self.assertTrue(all(value == 20 for value in per_day.values()))
        # Ganesh Chaturthi is evening-only in the frozen MCX calendar but still has 20 feasible slots.
        self.assertEqual(len(forward.scheduled_clicks_for_day(date(2026, 9, 14))), 20)

    def test_capture_is_fingerprinted_before_future_outcome(self):
        click = forward.phase_schedule()[0]["click_timestamp"]
        capture = forward.capture_shadow_direction(
            click_timestamp=click,
            snapshot=_snapshot(click),
            profile=_profile(),
            context_records=[],
            direction_memory_cases=[],
        )
        self.assertEqual(capture["direction"], "BULLISH")
        self.assertEqual(capture["direction_confidence"], "MODERATE")
        self.assertEqual(capture["supporting_families"], ["LOCAL_STRUCTURE", "PARTICIPATION"])
        self.assertEqual(len(capture["capture_fingerprint"]), 64)
        self.assertEqual(capture["decision_effect"], "NONE")
        self.assertEqual(capture["geometry_effect"], "NONE")
        self.assertEqual(capture["option_effect"], "NONE")
        with self.assertRaises(ValueError):
            forward.capture_shadow_direction(
                click_timestamp="2026-09-03T09:05:00+05:30",
                snapshot=_snapshot("2026-09-03T09:05:00+05:30"),
                profile=_profile(),
                context_records=[],
                direction_memory_cases=[],
            )

    def test_future_returns_mature_separately_at_exact_bar_completion(self):
        click = forward.phase_schedule()[0]["click_timestamp"]
        capture = forward.capture_shadow_direction(
            click_timestamp=click,
            snapshot=_snapshot(click),
            profile=_profile(),
            context_records=[],
            direction_memory_cases=[],
        )
        pending = forward.mature_underlying_outcome(
            capture,
            _future_candles(click),
            as_of=(parse_ist_timestamp(click) + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(pending["horizons"]["15"]["status"], "MATURED")
        self.assertEqual(pending["horizons"]["30"]["status"], "MATURED")
        self.assertEqual(pending["horizons"]["60"]["status"], "PENDING")
        matured = forward.mature_underlying_outcome(
            capture,
            _future_candles(click),
            as_of=(parse_ist_timestamp(click) + timedelta(minutes=120)).isoformat(),
        )
        self.assertEqual(matured["horizons"]["60"]["status"], "MATURED")
        self.assertGreater(matured["horizons"]["60"]["underlying_return_pct"], 0)
        self.assertFalse(matured["trade_outcome_used"])
        self.assertFalse(matured["geometry_used"])
        self.assertFalse(matured["option_pnl_used"])

    def test_phase_review_requires_frozen_coverage_and_directional_sample(self):
        captures = []
        outcomes = []
        for index, scheduled in enumerate(forward.phase_schedule()):
            fingerprint = f"capture-{index}"
            captures.append({
                "mode": forward.MODE,
                "click_timestamp": scheduled["click_timestamp"],
                "capture_fingerprint": fingerprint,
                "direction": "BULLISH",
                "direction_confidence": "MODERATE",
            })
            outcomes.append({
                "capture_fingerprint": fingerprint,
                "horizons": {
                    str(minutes): {"status": "MATURED", "underlying_return_pct": 1.0}
                    for minutes in forward.HORIZONS
                },
            })
        report = forward.evaluate_phase(captures, outcomes)
        self.assertEqual(report["status"], "READY_FOR_REVIEW")
        self.assertEqual(report["coverage"]["captured_clicks"], 200)
        self.assertEqual(report["horizon_score"]["60"]["correct"], 200)
        self.assertTrue(report["descriptive_evidence"]["primary_accuracy_lower_95_above_50pct"])
        self.assertFalse(report["promotion_allowed"])
        self.assertFalse(report["threshold_search_performed"])

    def test_incomplete_phase_cannot_be_called_ready(self):
        scheduled = forward.phase_schedule()[0]
        capture = {
            "mode": forward.MODE,
            "click_timestamp": scheduled["click_timestamp"],
            "capture_fingerprint": "one",
            "direction": "BULLISH",
            "direction_confidence": "MODERATE",
        }
        outcome = {
            "capture_fingerprint": "one",
            "horizons": {"60": {"status": "MATURED", "underlying_return_pct": 1.0}},
        }
        report = forward.evaluate_phase([capture], [outcome])
        self.assertEqual(report["status"], "COLLECTING")
        self.assertFalse(report["gates"]["data_sufficient_for_review"])

    def test_forward_module_has_no_current_mind_or_trade_execution_dependency(self):
        source = inspect.getsource(forward)
        self.assertNotIn("crude_oil_mini_current_mind_click", source)
        self.assertNotIn("build_current_mind_decision", source)
        self.assertNotIn("review_setup_risk", source)
        self.assertNotIn("option_brain", source.lower().replace('"option_effect"', ''))
        contract = forward.preregistration_contract()
        self.assertFalse(contract["promotion_allowed"])
        self.assertFalse(contract["current_mind_mutation_allowed"])
        self.assertFalse(contract["geometry_tuning_allowed"])
        self.assertFalse(contract["option_pnl_tuning_allowed"])


if __name__ == "__main__":
    unittest.main()
