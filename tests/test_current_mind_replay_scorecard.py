import unittest
from app.current_mind_replay_scorecard import *
class ScorecardTests(unittest.TestCase):
 def test_no_trade_not_penalized_for_no_trigger(self):
  s=score_decision_process({"action":"NO_TRADE","thesis":"conflicted","contradictions":[],"missing_context":[]})
  self.assertTrue(s["checks"]["trigger_defined"])
 def test_objective_not_prediction(self):
  self.assertIn("NOT_EXACT_PREDICTION",replay_scorecard([])["primary_objective"])
if __name__=="__main__":unittest.main()
