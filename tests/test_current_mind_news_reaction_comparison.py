from __future__ import annotations

import copy
import unittest

from app.current_mind_news_reaction_comparison import (
    _reaction_context_for_click,
    compare_no_news_vs_reaction_guard,
    reaction_guard_action,
)


def _reaction(direction="UP", *, anchor="2026-08-10T09:00:00+05:30", assimilation="2026-08-10T10:00:00+05:30", headline="event"):
    return {
        "coverage_status": "CLASSIFIABLE",
        "event": {
            "headline": headline,
            "source": "Reuters",
            "stance": "UNKNOWN",
            "disposition": "CONTEXT_ONLY",
            "materiality": "HIGH",
        },
        "window": {
            "event_timestamp": anchor,
            "reaction_anchor_timestamp": anchor,
            "assimilation": {"timestamp": assimilation},
        },
        "materiality_qualified_path": {
            "observation_status": "OBSERVED",
            "qualified_path_state": "UP_FOLLOW_THROUGH" if direction == "UP" else "DOWN_FOLLOW_THROUGH",
            "qualified_directions": {"immediate": direction, "confirmation": direction, "assimilation": direction},
        },
    }


def _journal(click, action, result="STOP", realized_r=-1.0):
    return {
        "click_timestamp": click,
        "decision": {"action": action},
        "outcome": {"result": result, "realized_r": realized_r},
    }


class CurrentMindNewsReactionComparisonTests(unittest.TestCase):
    def test_reaction_is_invisible_until_assimilation_and_expires_after_frozen_horizon(self):
        records = [_reaction("UP")]
        before = _reaction_context_for_click(records, "2026-08-10T09:55:00+05:30")
        active = _reaction_context_for_click(records, "2026-08-10T10:05:00+05:30")
        expired = _reaction_context_for_click(records, "2026-08-10T17:05:00+05:30")
        self.assertEqual(before["direction"], "UNKNOWN")
        self.assertEqual(active["direction"], "BULLISH")
        self.assertEqual(expired["direction"], "UNKNOWN")

    def test_guard_can_only_delay_an_opposing_existing_trade(self):
        bullish = {"direction": "BULLISH"}
        self.assertEqual(reaction_guard_action("BUY_PE", bullish)["action"], "WAIT")
        self.assertEqual(reaction_guard_action("BUY_CE", bullish)["action"], "BUY_CE")
        self.assertEqual(reaction_guard_action("NO_TRADE", bullish)["action"], "NO_TRADE")
        self.assertEqual(reaction_guard_action("WAIT", bullish)["action"], "WAIT")
        self.assertFalse(reaction_guard_action("NO_TRADE", bullish)["changed"])

    def test_conflicting_active_reactions_do_not_choose_a_direction(self):
        records = [
            _reaction("UP", headline="up"),
            _reaction("DOWN", headline="down"),
        ]
        context = _reaction_context_for_click(records, "2026-08-10T10:30:00+05:30")
        self.assertEqual(context["state"], "CONFLICTING_MATERIAL_REACTIONS")
        self.assertEqual(context["direction"], "UNKNOWN")
        self.assertEqual(reaction_guard_action("BUY_CE", context)["action"], "BUY_CE")

    def test_outcome_changes_cannot_change_overlay_decisions(self):
        baseline = {
            "decisions": [
                _journal("2026-08-10T10:30:00+05:30", "BUY_PE", "STOP", -1.0),
                _journal("2026-08-10T11:00:00+05:30", "BUY_CE", "TARGET", 1.5),
                _journal("2026-08-10T11:30:00+05:30", "NO_TRADE", "UNKNOWN", 0.0),
            ]
        }
        reaction_audit = {"records": [_reaction("UP")]}
        first = compare_no_news_vs_reaction_guard(baseline, reaction_audit)
        mutated = copy.deepcopy(baseline)
        mutated["decisions"][0]["outcome"] = {"result": "TARGET", "realized_r": 1.5}
        mutated["decisions"][1]["outcome"] = {"result": "STOP", "realized_r": -1.0}
        second = compare_no_news_vs_reaction_guard(mutated, reaction_audit)
        sig1 = [(row["click_timestamp"], row["baseline_action"], row["reaction_guard_action"]) for row in first["changed_rows"]]
        sig2 = [(row["click_timestamp"], row["baseline_action"], row["reaction_guard_action"]) for row in second["changed_rows"]]
        self.assertEqual(sig1, sig2)
        self.assertEqual(first["changed_clicks"], 1)
        self.assertEqual(second["changed_clicks"], 1)
        self.assertTrue(first["outcome_integrity"]["outcomes_read_for_overlay_decision"] is False)


if __name__ == "__main__":
    unittest.main()
