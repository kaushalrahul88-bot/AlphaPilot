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
            "pre_event": {"timestamp": "2026-08-10T08:55:00+05:30", "price": 100.0},
            "assimilation": {
                "timestamp": assimilation,
                "price": 102.0 if direction == "UP" else 98.0,
            },
        },
        "materiality_qualified_path": {
            "observation_status": "OBSERVED",
            "qualified_path_state": "UP_FOLLOW_THROUGH" if direction == "UP" else "DOWN_FOLLOW_THROUGH",
            "qualified_directions": {"immediate": direction, "confirmation": direction, "assimilation": direction},
        },
    }


def _journal(click, action, result="STOP", realized_r=-1.0, structure="RANGE"):
    return {
        "click_timestamp": click,
        "decision": {"action": action},
        "regime": {"observations": {"trend_structure": structure}},
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

    def test_catalyst_control_shadow_is_outcome_blind_and_does_not_change_v1_action(self):
        baseline = {
            "decisions": [
                _journal("2026-08-10T10:30:00+05:30", "BUY_PE", "STOP", -1.0, "UPTREND"),
            ]
        }
        reaction_audit = {"records": [_reaction("UP")]}
        candles = [
            {"timestamp": "2026-08-10T10:00:00+05:30", "close": 102.0},
            {"timestamp": "2026-08-10T10:30:00+05:30", "close": 102.5},
            {"timestamp": "2026-08-10T11:00:00+05:30", "close": 99.0},
        ]
        first = compare_no_news_vs_reaction_guard(baseline, reaction_audit, candles=candles)
        row = first["rows"][0]
        self.assertEqual(row["reaction_guard_action"], "WAIT")
        self.assertEqual(row["catalyst_control_shadow"]["state"], "CONTROL_ACTIVE")
        self.assertTrue(first["catalyst_control_shadow_policy"]["changes_v1_guard_action"] is False)

        mutated = copy.deepcopy(baseline)
        mutated["decisions"][0]["outcome"] = {"result": "TARGET", "realized_r": 1.5}
        second = compare_no_news_vs_reaction_guard(mutated, reaction_audit, candles=candles)
        self.assertEqual(
            first["rows"][0]["catalyst_control_shadow"],
            second["rows"][0]["catalyst_control_shadow"],
        )
        self.assertEqual(first["rows"][0]["reaction_guard_action"], second["rows"][0]["reaction_guard_action"])


if __name__ == "__main__":
    unittest.main()
