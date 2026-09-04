from __future__ import annotations

import unittest

from app.crude_oil_mini_prospective_memory_v1 import MIN_READY_CASES
from app.crude_oil_mini_research_status_v1 import build_research_status


class CrudeOilMiniResearchStatusV1Tests(unittest.TestCase):
    def test_validation_remains_locked_below_minimum_cases(self):
        status = build_research_status(
            {
                "status": "ACTIVE",
                "episode_count": 7,
                "outcome_rows": 16,
                "primary_diagnosis": {"NO_LARGE_CLEAN_MOVE_AFTER_ABSTENTION": 3},
            },
            {
                "primary_resolved_cases": MIN_READY_CASES - 1,
                "primary_non_resolved_cases": 0,
                "primary_missed_clean_moves": 2,
            },
        )

        self.assertEqual(status["validation"]["stage"], "ACCUMULATING_PROSPECTIVE_CASES")
        self.assertFalse(status["validation"]["descriptive_validation_ready"])
        self.assertEqual(status["pipeline"]["validate"], "LOCKED_ACCUMULATING_DATA")
        self.assertEqual(status["pipeline"]["improve"], "LOCKED")
        self.assertFalse(status["promotion_eligible"])
        self.assertFalse(status["live_execution_enabled"])
        self.assertFalse(status["broker_order_placement_enabled"])

    def test_twenty_cases_only_unlocks_descriptive_validation(self):
        status = build_research_status(
            {"status": "ACTIVE", "episode_count": MIN_READY_CASES + 4},
            {
                "primary_resolved_cases": MIN_READY_CASES,
                "primary_non_resolved_cases": 1,
                "primary_missed_clean_moves": 3,
            },
        )

        self.assertEqual(status["validation"]["progress_pct"], 100.0)
        self.assertTrue(status["validation"]["descriptive_validation_ready"])
        self.assertEqual(status["pipeline"]["validate"], "READY")
        self.assertFalse(status["validation"]["improvement_unlocked"])
        self.assertFalse(status["validation"]["holdout_test_unlocked"])
        self.assertFalse(status["validation"]["prospective_test_unlocked"])
        self.assertFalse(status["validation"]["promotion_eligible"])
        self.assertEqual(status["decision_effect"], "NONE")

    def test_progress_is_bounded_at_one_hundred_percent(self):
        status = build_research_status(
            {"status": "ACTIVE", "episode_count": 99},
            {
                "primary_resolved_cases": MIN_READY_CASES * 3,
                "primary_non_resolved_cases": 0,
                "primary_missed_clean_moves": 0,
            },
        )
        self.assertEqual(status["validation"]["progress_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
