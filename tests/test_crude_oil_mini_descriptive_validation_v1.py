from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from app.crude_oil_mini_descriptive_validation_v1 import build_descriptive_validation
from app.crude_oil_mini_prospective_memory_v1 import MIN_READY_CASES


IST = ZoneInfo("Asia/Kolkata")


def _row(index: int, *, action: str = "NO_TRADE", diagnosis: str = "NO_LARGE_CLEAN_MOVE_AFTER_ABSTENTION") -> dict:
    click = datetime(2026, 9, 4, 9, 0, tzinfo=IST) + timedelta(minutes=30 * index)
    direction = "BULLISH" if action == "BUY_CE" else "BEARISH" if action == "BUY_PE" else None
    return {
        "episode_id": f"episode-{index}",
        "click_at": click,
        "action": action,
        "direction": direction,
        "evidence_quality": "COHERENT" if direction else "CONFLICTED",
        "integrated_v2_direction": direction if index % 2 == 0 else "UNKNOWN",
        "integrated_v2_confidence": "MODERATE",
        "available_at": click + timedelta(minutes=120),
        "geometry_outcome": "TARGET_FIRST" if action in {"BUY_CE", "BUY_PE"} else "NOT_APPLICABLE",
        "diagnosis": "TRADE_EPISODE" if action in {"BUY_CE", "BUY_PE"} else diagnosis,
        "underlying_return_pct": 1.0 if action == "BUY_CE" else -1.0 if action == "BUY_PE" else 0.2,
        "max_up_atr": 1.8,
        "max_down_atr": 0.4,
        "option_observations": 2 if action in {"BUY_CE", "BUY_PE"} else 0,
        "option_return_pct": 12.0 if action in {"BUY_CE", "BUY_PE"} else None,
    }


class CrudeOilMiniDescriptiveValidationV1Tests(unittest.TestCase):
    def test_partial_results_are_withheld_below_gate(self):
        rows = [_row(index, action="BUY_CE" if index % 3 == 0 else "NO_TRADE") for index in range(MIN_READY_CASES - 1)]
        report = build_descriptive_validation(rows, as_of="2026-09-05T12:00:00+05:30")

        self.assertEqual(report["status"], "LOCKED_ACCUMULATING_DATA")
        self.assertIsNone(report["report"])
        self.assertFalse(report["partial_performance_metrics_exposed"])
        self.assertFalse(report["improvement_unlocked"])
        self.assertFalse(report["promotion_eligible"])
        self.assertFalse(report["statistical_edge_claim_allowed"])

    def test_gate_exposes_only_descriptive_preregistered_metrics(self):
        rows = []
        for index in range(MIN_READY_CASES):
            if index < 5:
                rows.append(_row(index, action="BUY_CE"))
            elif index < 8:
                rows.append(_row(index, action="BUY_PE"))
            elif index == 8:
                rows.append(_row(index, diagnosis="MISSED_BULLISH_CLEAN_EXPANSION"))
            elif index == 9:
                rows.append(_row(index, diagnosis="MISSED_BEARISH_CLEAN_EXPANSION"))
            else:
                rows.append(_row(index))

        report = build_descriptive_validation(rows, as_of="2026-09-06T12:00:00+05:30")
        detail = report["report"]

        self.assertEqual(report["status"], "READY_FOR_DESCRIPTIVE_VALIDATION")
        self.assertTrue(report["partial_performance_metrics_exposed"])
        self.assertEqual(detail["sample"]["resolved_primary_cases"], MIN_READY_CASES)
        self.assertEqual(detail["sample"]["trade_episodes"], 8)
        self.assertEqual(detail["trade_geometry"]["target_first"], 8)
        self.assertEqual(detail["abstention_diagnostics"]["missed_bullish_clean_expansion"], 1)
        self.assertEqual(detail["abstention_diagnostics"]["missed_bearish_clean_expansion"], 1)
        self.assertEqual(detail["option_translation_observation"]["eligible_trade_episodes"], 8)
        self.assertEqual(detail["trade_geometry"]["average_directional_underlying_return_pct"], 1.0)
        self.assertFalse(report["statistical_edge_claim_allowed"])
        self.assertFalse(report["improvement_unlocked"])
        self.assertFalse(report["holdout_test_unlocked"])
        self.assertFalse(report["prospective_test_unlocked"])
        self.assertFalse(report["promotion_eligible"])

    def test_wait_and_no_trade_are_not_treated_as_zero_return_trades(self):
        rows = [_row(index, action="NO_TRADE") for index in range(MIN_READY_CASES)]
        report = build_descriptive_validation(rows, as_of="2026-09-06T12:00:00+05:30")
        detail = report["report"]

        self.assertEqual(detail["sample"]["trade_episodes"], 0)
        self.assertEqual(detail["sample"]["abstention_episodes"], MIN_READY_CASES)
        self.assertIsNone(detail["trade_geometry"]["average_directional_underlying_return_pct"])
        self.assertEqual(detail["abstention_diagnostics"]["no_large_clean_move"], MIN_READY_CASES)

    def test_ambiguous_geometry_is_separate_from_target_first(self):
        rows = [_row(index, action="BUY_CE") for index in range(MIN_READY_CASES)]
        rows[0]["geometry_outcome"] = "ENTRY_AND_EXIT_SAME_BAR_AMBIGUOUS"
        rows[1]["geometry_outcome"] = "STOP_TARGET_SAME_BAR_AMBIGUOUS"
        report = build_descriptive_validation(rows, as_of="2026-09-06T12:00:00+05:30")
        geometry = report["report"]["trade_geometry"]

        self.assertEqual(geometry["ambiguous"], 2)
        self.assertEqual(geometry["target_first"], MIN_READY_CASES - 2)


if __name__ == "__main__":
    unittest.main()
