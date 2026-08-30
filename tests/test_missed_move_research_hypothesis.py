import unittest
from app.missed_move_research_hypothesis import *
class HypothesisTests(unittest.TestCase):
 def row(self,i):return {"decision_fingerprint":str(i),"failure_modes":{"PERCEPTION_GAP":[{"factor":"rising participation"}]}}
 def test_three_repeats_create_research_not_rule(self):
  r=build_missed_move_hypotheses([self.row(1),self.row(2),self.row(3)])
  self.assertEqual(r["hypotheses"][0]["status"],"RESEARCH_ONLY")
 def test_single_miss_does_not_promote(self):
  self.assertEqual(build_missed_move_hypotheses([self.row(1)])["hypotheses"],[])
if __name__=="__main__":unittest.main()
