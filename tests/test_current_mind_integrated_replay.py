import unittest
from app.current_mind_integrated_replay import current_mind_click,_memory_case_available_at,_visible_memory_cases
from app.trader_decision_journal import journal_decision


class IntegratedTests(unittest.TestCase):
 def test_click_is_journaled_without_fabricated_trade(self):
  x=current_mind_click(click_timestamp="2026-08-25T14:00:00+05:30",context_records=[],market_features={},evidence_items=[])
  self.assertEqual(x["decision"]["action"],"NO_TRADE");self.assertTrue(x["decision_fingerprint"])
  self.assertEqual(x["playbook_semantics"]["status"],"NO_DECLARED_PLAYBOOK")
  self.assertEqual(x["memory_visibility"]["provided_cases"],0)
 def test_annotations_do_not_change_frozen_decision_fingerprint(self):
  x=current_mind_click(click_timestamp="2026-08-25T14:00:00+05:30",context_records=[],market_features={},evidence_items=[])
  frozen=journal_decision(click_timestamp=x["click_timestamp"],information_board=x["information_board"],
    regime=x["regime"],evidence=x["evidence"],scenario=x["scenario"],thesis=x["thesis"],
    decision=x["decision"],option_expression=x["option_expression"])
  self.assertEqual(x["decision_fingerprint"],frozen["decision_fingerprint"])
  self.assertNotIn("playbook_semantics",frozen)
  self.assertNotIn("memory_visibility",frozen)
 def test_only_resolved_prior_cases_are_visible(self):
  cases=[
   {"outcome":{"result":"TARGET","exit_at":"2026-08-25T10:30:00+05:30"}},
   {"outcome":{"result":"STOP","exit_at":"2026-08-25T12:00:00+05:30"}},
   {"outcome":{"future_move_without_setup":True}},
  ]
  visible=_visible_memory_cases(cases,"2026-08-25T11:00:00+05:30")
  self.assertEqual(visible,[cases[0]])
 def test_same_timestamp_resolution_is_withheld(self):
  case={"outcome":{"result":"TARGET","exit_at":"2026-08-25T11:00:00+05:30"}}
  self.assertEqual(_visible_memory_cases([case],"2026-08-25T11:00:00+05:30"),[])
 def test_explicit_availability_can_admit_non_trade_case(self):
  case={"available_at":"2026-08-25T10:59:00+05:30","outcome":{"future_move_without_setup":True}}
  self.assertEqual(_visible_memory_cases([case],"2026-08-25T11:00:00+05:30"),[case])
 def test_unknown_outcome_availability_fails_closed(self):
  self.assertIsNone(_memory_case_available_at({"outcome":{"result":"SESSION_END"}}))
if __name__=="__main__":unittest.main()
