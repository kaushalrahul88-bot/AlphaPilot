from __future__ import annotations

import unittest

from app.crude_oil_mini_literal_playbook_shadow import (
    evaluate_literal_playbook_shadow,
    literal_playbook_confirmation,
)


class CrudeOilMiniLiteralPlaybookShadowTests(unittest.TestCase):
    def _row(self, playbook, action="BUY_CE", **features):
        base = {
            "structure": "UPTREND",
            "atr_pct": 0.20,
            "atr_points": 15.0,
            "ema20_gap_pct": 0.10,
            "return_15m_pct": 0.15,
            "return_60m_pct": 0.40,
            "opening_range_break": "ABOVE",
            "opening_range_high": 7000.0,
            "opening_range_low": 6900.0,
            "price": 7008.0,
            "session_range_position": 0.50,
        }
        base.update(features)
        return {
            "session": "2026-08-31",
            "click_timestamp": "2026-08-31T12:00:00+05:30",
            "action": action,
            "decision": {"playbook": playbook},
            "features": base,
            "profile": {"range_position_low": 0.15, "range_position_high": 0.85},
            "future_returns_pct": {"60": 0.25},
            "outcome": {"result": "TARGET", "realized_r": 1.5},
            "decision_fingerprint": "abc",
        }

    def test_trend_pullback_requires_reacceptance_near_ema20(self):
        row = self._row("TREND_PULLBACK")
        result = literal_playbook_confirmation(row)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["scale_source"], "CONTEMPORANEOUS_ATR")

        row["features"]["ema20_gap_pct"] = 0.50
        result = literal_playbook_confirmation(row)
        self.assertFalse(result["confirmed"])
        self.assertIn("within_one_atr_of_ema20", result["failed_checks"])

    def test_breakout_retest_must_match_breakout_direction(self):
        row = self._row("BREAKOUT_RETEST", opening_range_break="BELOW")
        result = literal_playbook_confirmation(row)
        self.assertFalse(result["confirmed"])
        self.assertIn("breakout_direction_matches_action", result["failed_checks"])

    def test_breakout_retest_confirms_near_broken_boundary(self):
        row = self._row("BREAKOUT_RETEST", price=7005.0, opening_range_break="ABOVE")
        result = literal_playbook_confirmation(row)
        self.assertTrue(result["confirmed"])

    def test_range_edge_reversal_uses_causal_profile_edge(self):
        row = self._row(
            "RANGE_EDGE_REVERSAL",
            structure="RANGE",
            session_range_position=0.10,
            return_15m_pct=0.20,
        )
        result = literal_playbook_confirmation(row)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["scale_source"], "PRIOR_COMPLETE_SESSION_RANGE_QUANTILES")

    def test_shadow_does_not_change_strategy(self):
        row = self._row("TREND_PULLBACK")
        baseline = {
            "reference_contract": "CRUDEOILM21SEP26FUT",
            "evaluated_clicks": 1,
            "complete_session_dates": ["2026-08-31"],
            "decisions": [row],
        }
        result = evaluate_literal_playbook_shadow(baseline)
        self.assertFalse(result["strategy_rules_changed"])
        self.assertEqual(result["decision_effect"], "SHADOW_ONLY")
        self.assertEqual(result["literal_confirmed_setups"], 1)
        self.assertEqual(result["annotations"][0]["decision_fingerprint"], "abc")


if __name__ == "__main__":
    unittest.main()
