import unittest
from app.current_mind_integrated_replay import current_mind_click
from app.trader_decision_journal import journal_decision
class IntegratedTests(unittest.TestCase):
 def test_click_is_journaled_without_fabricated_trade(self):
  x=current_mind_click(click_timestamp="2026-08-25T14:00:00+05:30",context_records=[],market_features={},evidence_items=[])
  self.assertEqual(x["decision"]["action"],"NO_TRADE");self.assertTrue(x["decision_fingerprint"])
  self.assertEqual(x["playbook_semantics"]["status"],"NO_DECLARED_PLAYBOOK")
 def test_semantics_annotation_does_not_change_frozen_decision_fingerprint(self):
  x=current_mind_click(click_timestamp="2026-08-25T14:00:00+05:30",context_records=[],market_features={},evidence_items=[])
  frozen=journal_decision(click_timestamp=x["click_timestamp"],information_board=x["information_board"],
    regime=x["regime"],evidence=x["evidence"],scenario=x["scenario"],thesis=x["thesis"],
    decision=x["decision"],option_expression=x["option_expression"])
  self.assertEqual(x["decision_fingerprint"],frozen["decision_fingerprint"])
  self.assertNotIn("playbook_semantics",frozen)
if __name__=="__main__":unittest.main()
