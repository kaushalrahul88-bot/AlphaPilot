import unittest

from app.asgi import _forward_phase1_operational_view


class CurrentMindForwardRunnerTests(unittest.TestCase):
    def test_operational_view_seals_all_score_fields(self):
        result = {
            "mode": "COPPER_CURRENT_MIND_FORWARD_PHASE1_V1",
            "research_only": True,
            "production_rules_changed": False,
            "live_execution_enabled": False,
            "as_of": "2026-09-03T23:40:00+05:30",
            "reference_contract": "COPPER30SEP26FUT",
            "contract_metadata": {"trading_symbol": "COPPER30SEP26FUT"},
            "bar_timing": "CANDLE_START_PLUS_5_MINUTES",
            "eligible_sessions": ["2026-09-02", "2026-09-03"],
            "excluded_sessions": [],
            "phase1_complete": False,
            "scheduled_clicks": 40,
            "evaluated_clicks": 40,
            "click_coverage_exact": True,
            "validation_status": "WAITING_FOR_PREREGISTERED_FORWARD_SAMPLE",
            "actions": {"BUY_CE": 10, "BUY_PE": 10, "NO_TRADE": 20},
            "trades": 20,
            "resolved_trades": 12,
            "targets": 7,
            "stops": 5,
            "no_entry": 3,
            "session_end": 5,
            "expectancy_r_resolved": 0.25,
            "scorecard": {"secret": "interim-score"},
            "decisions": [{"secret": "interim-decision"}],
        }
        view = _forward_phase1_operational_view(
            {"status": "COMPLETED", "result": result, "error": None}
        )
        self.assertEqual(view["status"], "COMPLETED")
        self.assertEqual(view["reference_contract"], "COPPER30SEP26FUT")
        self.assertFalse(view["score_revealed"])
        for key in (
            "actions", "trades", "resolved_trades", "targets", "stops",
            "no_entry", "session_end", "expectancy_r_resolved", "scorecard", "decisions",
        ):
            self.assertNotIn(key, view)
            self.assertIn(key, view["sealed_fields"])

    def test_running_and_failed_views_never_echo_result(self):
        running = _forward_phase1_operational_view(
            {"status": "RUNNING", "result": {"targets": 99}, "error": None}
        )
        failed = _forward_phase1_operational_view(
            {"status": "FAILED", "result": {"targets": 99}, "error": "auth failed"}
        )
        self.assertEqual(running, {"status": "RUNNING", "error": None, "score_revealed": False})
        self.assertEqual(
            failed,
            {"status": "FAILED", "error": "auth failed", "score_revealed": False},
        )
        self.assertNotIn("targets", running)
        self.assertNotIn("targets", failed)


if __name__ == "__main__":
    unittest.main()
