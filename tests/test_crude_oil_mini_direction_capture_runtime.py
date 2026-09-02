from __future__ import annotations

import inspect
import unittest
from datetime import timedelta

from app import crude_oil_mini_direction_capture_runtime as runtime
from app import crude_oil_mini_direction_capture_store as capture_store
from app.commodity_time import parse_ist_timestamp


class CrudeOilMiniDirectionCaptureRuntimeTests(unittest.TestCase):
    def test_schedule_capture_window_fails_closed_after_ten_minutes(self):
        scheduled = runtime.phase_schedule()[0]
        click = parse_ist_timestamp(scheduled["click_timestamp"])

        before = runtime.classify_schedule([scheduled], click - timedelta(seconds=1))
        self.assertEqual(len(before["future"]), 1)

        due = runtime.classify_schedule([scheduled], click + timedelta(minutes=9, seconds=59))
        self.assertEqual(len(due["due"]), 1)

        expired = runtime.classify_schedule([scheduled], click + timedelta(minutes=10))
        self.assertEqual(len(expired["expired"]), 1)

        captured = runtime.classify_schedule(
            [scheduled],
            click + timedelta(minutes=30),
            captured_clicks=[scheduled["click_timestamp"]],
        )
        self.assertEqual(len(captured["resolved"]), 1)
        self.assertFalse(captured["expired"])

        missed = runtime.classify_schedule(
            [scheduled],
            click + timedelta(minutes=30),
            missed_clicks=[scheduled["click_timestamp"]],
        )
        self.assertEqual(len(missed["resolved"]), 1)
        self.assertFalse(missed["expired"])

    def test_completed_hour_context_adapter_never_uses_future_bar(self):
        click = "2026-09-03T16:00:00+05:30"
        probe = {
            "feeds": {
                "WTI_CRUDE": {
                    "status": "AVAILABLE",
                    "data": [
                        {"bar_start": "2026-09-03T13:00:00+05:30", "available_at": "2026-09-03T14:00:00+05:30", "close": 70.0},
                        {"bar_start": "2026-09-03T14:00:00+05:30", "available_at": "2026-09-03T15:00:00+05:30", "close": 71.0},
                        {"bar_start": "2026-09-03T15:00:00+05:30", "available_at": "2026-09-03T16:00:00+05:30", "close": 72.0},
                        # Must be invisible at the 16:00 click even though supplied by the probe response.
                        {"bar_start": "2026-09-03T16:00:00+05:30", "available_at": "2026-09-03T17:00:00+05:30", "close": 60.0},
                    ],
                },
                "BRENT_CRUDE": {"status": "UNAVAILABLE", "data": []},
                "USDINR": {"status": "UNAVAILABLE", "data": []},
                "DXY": {"status": "UNAVAILABLE", "data": []},
            }
        }
        records = runtime.context_records_from_probe(probe, click)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["series"], "WTI_CRUDE")
        self.assertEqual(record["available_at"], click)
        self.assertEqual(record["value"]["close"], 72.0)
        self.assertEqual(record["value"]["stance"], "BULLISH")
        self.assertGreater(record["value"]["return_1h_pct"], 0)

    def test_operational_view_keeps_direction_scores_sealed(self):
        report = {
            "status": "COLLECTING",
            "validation_phase": runtime.VALIDATION_PHASE,
            "coverage": {"captured_clicks": 20},
            "requirements": {"minimum_primary_directional_calls": 50},
            "gates": {"data_sufficient_for_review": False},
            "horizon_score": {"60": {"accuracy_pct": 99.0}},
            "confidence_score": {"STRONG": {"accuracy_pct": 99.0}},
            "descriptive_evidence": {"primary_accuracy_lower_95_above_50pct": True},
        }
        view = runtime._operational_phase_view(report)
        self.assertFalse(view["score_revealed"])
        self.assertNotIn("horizon_score", view)
        self.assertNotIn("confidence_score", view)
        self.assertNotIn("descriptive_evidence", view)
        self.assertFalse(view["promotion_allowed"])

    def test_capture_store_schema_is_append_only(self):
        normalized = " ".join(capture_store.SCHEMA_SQL.upper().split())
        self.assertNotIn(" DO UPDATE ", normalized)
        self.assertNotIn("UPDATE CRUDE_OIL_MINI_DIRECTION_V2", normalized)
        self.assertIn("CRUDE_OIL_MINI_DIRECTION_V2_CAPTURES", normalized)
        self.assertIn("CRUDE_OIL_MINI_DIRECTION_V2_OUTCOMES", normalized)
        self.assertIn("CRUDE_OIL_MINI_DIRECTION_V2_CAPTURE_MISSES", normalized)

    def test_runtime_has_no_current_mind_geometry_or_option_execution_dependency(self):
        source = inspect.getsource(runtime)
        self.assertNotIn("crude_oil_mini_current_mind_click", source)
        self.assertNotIn("build_current_mind_decision", source)
        self.assertNotIn("review_setup_risk", source)
        self.assertNotIn("option_brain", source.lower())
        contract = runtime.runtime_contract()
        self.assertFalse(contract["current_mind_mutation_allowed"])
        self.assertFalse(contract["live_execution_enabled"])
        self.assertFalse(contract["regular_crude_collector_changed"])
        self.assertFalse(contract["promotion_allowed"])
        self.assertEqual(contract["max_capture_lateness_minutes"], 10)


if __name__ == "__main__":
    unittest.main()
