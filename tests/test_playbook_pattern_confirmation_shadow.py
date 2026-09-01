from __future__ import annotations

import copy
import unittest

from app.playbook_pattern_confirmation_shadow import assess_declared_playbook_pattern


def _row(ts, o, h, l, c, v=100.0):
    return [ts, float(o), float(h), float(l), float(c), float(v), None]


def _journal(action, playbook, structure):
    return {
        "decision": {"action": action, "playbook": playbook},
        "regime": {"observations": {"trend_structure": structure}},
        "outcome": {"result": "STOP", "realized_r": -1.0},
    }


class PlaybookPatternConfirmationShadowTests(unittest.TestCase):
    def test_range_edge_reversal_requires_literal_edge_sweep_and_rejection(self):
        rows = [
            _row("2026-08-26T09:00:00+05:30", 100, 101, 99, 100),
            _row("2026-08-26T09:05:00+05:30", 100, 102, 99.5, 101),
            _row("2026-08-26T09:10:00+05:30", 101, 102.5, 100, 102),
            _row("2026-08-26T09:15:00+05:30", 102, 103, 101, 102.5),
            _row("2026-08-26T09:20:00+05:30", 102.5, 103.5, 102, 103),
            _row("2026-08-26T09:25:00+05:30", 103, 104, 101.5, 102),
        ]
        result = assess_declared_playbook_pattern(rows, 5, _journal("BUY_PE", "RANGE_EDGE_REVERSAL", "RANGE"))
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["status"], "PATTERN_CONFIRMED")
        self.assertEqual(result["detail"]["reason"], "HIGH_EDGE_SWEEP_REJECTION")

    def test_range_middle_does_not_confirm_range_edge_reversal(self):
        rows = [
            _row("2026-08-26T09:00:00+05:30", 100, 105, 95, 100),
            _row("2026-08-26T09:05:00+05:30", 100, 103, 98, 101),
            _row("2026-08-26T09:10:00+05:30", 101, 103, 99, 100),
        ]
        result = assess_declared_playbook_pattern(rows, 2, _journal("BUY_PE", "RANGE_EDGE_REVERSAL", "RANGE"))
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["detail"]["reason"], "NO_HIGH_EDGE_REJECTION")

    def test_breakout_retest_requires_break_retest_and_retention(self):
        rows = []
        for minute in range(0, 60, 5):
            rows.append(_row(f"2026-08-26T09:{minute:02d}:00+05:30", 100, 101, 99, 100))
        rows.extend([
            _row("2026-08-26T10:00:00+05:30", 100, 102, 100, 101.5),
            _row("2026-08-26T10:05:00+05:30", 101.5, 102, 100.8, 101.2),
            _row("2026-08-26T10:10:00+05:30", 101.2, 102.2, 101.0, 102.0),
        ])
        result = assess_declared_playbook_pattern(rows, len(rows) - 1, _journal("BUY_CE", "BREAKOUT_RETEST", "UPTREND"))
        self.assertTrue(result["confirmed"])
        self.assertTrue(result["detail"]["retest_seen"])

    def test_failed_breakout_requires_recent_outside_close_then_reclaim(self):
        rows = []
        for minute in range(0, 60, 5):
            rows.append(_row(f"2026-08-26T09:{minute:02d}:00+05:30", 100, 101, 99, 100))
        rows.extend([
            _row("2026-08-26T10:00:00+05:30", 100, 102, 100, 101.5),
            _row("2026-08-26T10:05:00+05:30", 101.5, 102, 100.5, 100.5),
        ])
        result = assess_declared_playbook_pattern(rows, len(rows) - 1, _journal("BUY_PE", "FAILED_BREAKOUT", "RANGE"))
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["detail"]["reason"], "FAILED_BREAKOUT_REJECTED")

    def test_outcome_mutation_cannot_change_pattern_shadow(self):
        rows = [
            _row("2026-08-26T09:00:00+05:30", 100, 101, 99, 100),
            _row("2026-08-26T09:05:00+05:30", 100, 102, 99.5, 101),
            _row("2026-08-26T09:10:00+05:30", 101, 102.5, 100, 102),
            _row("2026-08-26T09:15:00+05:30", 102, 103, 101, 102.5),
            _row("2026-08-26T09:20:00+05:30", 102.5, 103.5, 102, 103),
            _row("2026-08-26T09:25:00+05:30", 103, 104, 101.5, 102),
        ]
        journal = _journal("BUY_PE", "RANGE_EDGE_REVERSAL", "RANGE")
        first = assess_declared_playbook_pattern(rows, 5, journal)
        mutated = copy.deepcopy(journal)
        mutated["outcome"] = {"result": "TARGET", "realized_r": 1.5}
        second = assess_declared_playbook_pattern(rows, 5, mutated)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
