import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.copper_avoidance_forward_validation import (
    AVOIDANCE_HYPOTHESES_V1,
    RESEARCH_CUTOFF_AT,
    evaluate_forward_avoidance,
)


IST = ZoneInfo("Asia/Kolkata")


def _experience(stamp, *, avoided, forward):
    if avoided:
        session_position = 0.90
        vwap_gap = 0.20
        opening_range = "ABOVE"
    else:
        session_position = 0.60
        vwap_gap = 0.05
        opening_range = "INSIDE"
    features = {
        "timestamp": stamp.isoformat(),
        "price": 900.0,
        "structure": "UPTREND",
        "return_15m_pct": 0.10,
        "ema20_gap_pct": 0.10,
        "ema50_gap_pct": 0.20,
        "atr_pct": 0.20,
        "relative_volume": 1.10,
        "time_adjusted_relative_volume": 1.20,
        "session_range_position": session_position,
        "session_vwap_gap_pct": vwap_gap,
        "opening_range_break": opening_range,
        "price_oi_state": "UNKNOWN",
        "oi_change_15m_pct": None,
    }
    return {"features": features, "labels": {"forward_60m_pct": forward}}


class CopperAvoidanceForwardValidationTests(unittest.TestCase):
    def test_hypotheses_are_frozen_and_nonempty(self):
        self.assertGreaterEqual(len(AVOIDANCE_HYPOTHESES_V1), 5)
        ids = [item["id"] for item in AVOIDANCE_HYPOTHESES_V1]
        self.assertEqual(len(ids), len(set(ids)))

    def test_development_rows_before_cutoff_are_excluded(self):
        before = RESEARCH_CUTOFF_AT - timedelta(hours=1)
        report = evaluate_forward_avoidance([
            _experience(before, avoided=True, forward=-0.20),
        ])
        self.assertEqual(report["coverage"]["future_brain_a_signals"], 0)
        self.assertEqual(report["status"], "COLLECTING")
        self.assertFalse(report["gates"]["enough_fresh_data"])

    def test_fresh_harmful_contexts_can_validate_hypothesis(self):
        experiences = []
        start = datetime(2026, 8, 31, 10, 0, tzinfo=IST)
        for day in range(10):
            session = start + timedelta(days=day)
            for i in range(4):
                experiences.append(
                    _experience(session + timedelta(minutes=5*i), avoided=True, forward=-0.20)
                )
            for i in range(4, 8):
                experiences.append(
                    _experience(session + timedelta(minutes=5*i), avoided=False, forward=0.20)
                )
        report = evaluate_forward_avoidance(experiences)
        self.assertEqual(report["coverage"]["validation_trading_days"], 10)
        self.assertEqual(report["coverage"]["avoided_signals"], 40)
        self.assertTrue(report["gates"]["enough_fresh_data"])
        self.assertTrue(report["gates"]["avoided_contexts_remain_harmful"])
        self.assertTrue(report["gates"]["hypothetical_filter_improves_brain_a"])
        self.assertTrue(report["gates"]["validated"])
        self.assertEqual(report["status"], "VALIDATED_HYPOTHESIS")

    def test_no_production_role_is_granted(self):
        stamp = datetime(2026, 8, 31, 10, 0, tzinfo=IST)
        report = evaluate_forward_avoidance([
            _experience(stamp, avoided=True, forward=-0.20),
        ])
        self.assertTrue(report["research_only"])
        self.assertFalse(report["production_rules_changed"])
        self.assertFalse(report["live_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
