import unittest
from app.setup_playbook_selector import *
class PlaybookTests(unittest.TestCase):
 def test_extension_blocks_trend_pullback_until_reset(self):
  r=eligible_playbooks(["TRENDING","EXTENDED"])
  self.assertNotIn("TREND_PULLBACK",[x["playbook"] for x in r["eligible"]])
 def test_incomplete_setup_waits(self):
  self.assertEqual(setup_candidate("FAILED_BREAKOUT",thesis="failure")["status"],"WAIT")
 def test_default_playbook_is_labeled_hypothesis_not_literal_confirmation(self):
  r=playbook_selection_semantics("RANGE_EDGE_REVERSAL")
  self.assertEqual(r["status"],"REGIME_ELIGIBLE_HYPOTHESIS")
  self.assertEqual(r["selection_basis"],"REGIME_ELIGIBILITY_ONLY")
  self.assertEqual(r["literal_pattern_confirmation"],"NOT_VERIFIED_IN_DECISION_PATH")
  self.assertFalse(r["generic_confirmation_is_literal_pattern_confirmation"])
 def test_external_builder_semantics_are_not_inferred(self):
  r=playbook_selection_semantics("TREND_PULLBACK",default_selector=False)
  self.assertEqual(r["status"],"EXTERNAL_DECISION_BUILDER")
  self.assertEqual(r["selection_basis"],"NOT_INFERRED")
if __name__=="__main__":unittest.main()
