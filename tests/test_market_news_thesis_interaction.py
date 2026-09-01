from __future__ import annotations

import copy
import unittest

from app.market_news_thesis_interaction import (
    assess_news_thesis_interaction,
    audit_declared_playbook,
)


def _journal(*, action="BUY_PE", playbook="RANGE_EDGE_REVERSAL", location="IN_VALUE", outcome="STOP"):
    return {
        "click_timestamp": "2026-08-26T11:15:00+05:30",
        "decision": {
            "action": action,
            "playbook": playbook,
            "reason": "CURRENT_MIND_ACTIONABLE_SETUP",
            "entry_trigger": "Break and accept below 100.00",
            "invalidation": "Trade invalid above 101.00",
        },
        "regime": {
            "regime_labels": ["RANGING"],
            "observations": {
                "trend_structure": "RANGE",
                "location": location,
                "opening_behavior": "BALANCED",
            },
        },
        "outcome": {"result": outcome, "realized_r": -1.0 if outcome == "STOP" else 1.5},
    }


def _control(*, state="CONTROL_ACTIVE", direction="BULLISH", controls=True):
    return {
        "state": state,
        "direction": direction,
        "controls_direction": controls,
    }


class MarketNewsThesisInteractionTests(unittest.TestCase):
    def test_active_catalyst_opposing_range_reversal_is_classified_without_veto(self):
        journal = _journal()
        result = assess_news_thesis_interaction(journal, _control())
        self.assertEqual(result["interaction"], "ACTIVE_CATALYST_OPPOSES_REVERSAL_THESIS")
        self.assertEqual(result["alignment"], "OPPOSED")
        self.assertEqual(result["playbook_family"], "MEAN_REVERSION_REVERSAL")
        self.assertFalse(result["changes_decision"])
        self.assertTrue(result["outcome_blind"])

    def test_regime_eligibility_is_not_treated_as_pattern_confirmation(self):
        audit = audit_declared_playbook(_journal())
        self.assertTrue(audit["regime_requirements_satisfied"])
        self.assertEqual(audit["status"], "ACTIONABLE_PLAYBOOK_PATTERN_NOT_VERIFIED")
        self.assertFalse(audit["pattern_specific_confirmation_recorded"])
        self.assertEqual(audit["range_edge_location_proxy"], "NO_EDGE_LOCATION_PROXY")

    def test_range_edge_location_proxy_is_descriptive_not_semantic_proof(self):
        audit = audit_declared_playbook(_journal(location="EXTENDED_BELOW_VALUE"))
        self.assertEqual(audit["range_edge_location_proxy"], "EDGE_LIKE_LOCATION_PROXY")
        self.assertEqual(audit["status"], "ACTIONABLE_PLAYBOOK_PATTERN_NOT_VERIFIED")

    def test_outcome_mutation_cannot_change_shadow_state(self):
        journal = _journal(outcome="STOP")
        first = assess_news_thesis_interaction(journal, _control())
        mutated = copy.deepcopy(journal)
        mutated["outcome"] = {"result": "TARGET", "realized_r": 1.5}
        second = assess_news_thesis_interaction(mutated, _control())
        self.assertEqual(first, second)

    def test_active_aligned_catalyst_is_context_only_not_action_upgrade(self):
        journal = _journal(action="BUY_CE", playbook="RANGE_EDGE_REVERSAL")
        result = assess_news_thesis_interaction(journal, _control(direction="BULLISH"))
        self.assertEqual(result["interaction"], "ACTIVE_CATALYST_ALIGNS_WITH_THESIS")
        self.assertEqual(result["alignment"], "ALIGNED")
        self.assertFalse(result["changes_decision"])

    def test_nondirectional_baseline_remains_non_actionable(self):
        journal = _journal(action="NO_TRADE", playbook="")
        result = assess_news_thesis_interaction(journal, _control())
        self.assertEqual(result["interaction"], "BASELINE_NON_DIRECTIONAL")
        self.assertEqual(result["alignment"], "NOT_APPLICABLE")
        self.assertIsNone(result["playbook"])


if __name__ == "__main__":
    unittest.main()
