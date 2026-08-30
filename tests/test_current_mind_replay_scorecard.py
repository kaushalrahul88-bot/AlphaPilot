import unittest
from app.current_mind_replay_scorecard import *
class ScorecardTests(unittest.TestCase):
 def test_no_trade_not_penalized_for_no_trigger(self):
  s=score_decision_process({"action":"NO_TRADE","thesis":"conflicted","contradictions":[],"missing_context":[]})
  self.assertTrue(s["checks"]["trigger_defined"])
 def test_structured_outcome_result_is_scored(self):
  s=replay_scorecard([
   {"action":"BUY_CE","thesis":"x","entry_trigger":"e","invalidation":"s","target_or_exit_logic":"t","risk_reward_basis":"r","contradictions":[],"missing_context":[],"outcome":{"result":"TARGET","realized_r":1.5}},
   {"action":"BUY_PE","thesis":"x","entry_trigger":"e","invalidation":"s","target_or_exit_logic":"t","risk_reward_basis":"r","contradictions":[],"missing_context":[],"outcome":{"result":"STOP","realized_r":-1.0}},
  ])
  self.assertEqual(s["resolved_trades"],2)
  self.assertEqual(s["target_rate_resolved"],50.0)
 def test_objective_not_prediction(self):
  self.assertIn("NOT_EXACT_PREDICTION",replay_scorecard([])["primary_objective"])
if __name__=="__main__":unittest.main()
