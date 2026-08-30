import unittest
from app.trader_scenario_board import build_scenario_board,propose_action
class ScenarioTests(unittest.TestCase):
 def test_evidence_is_not_vote_count(self):
  b=build_scenario_board([{"stance":"BULLISH"},{"stance":"BEARISH"}])
  self.assertIn("Do not count",b["decision_policy"][0])
 def test_wait_for_confirmation(self):
  self.assertEqual(propose_action({},thesis="breakout",needs_confirmation=True)["action"],"WAIT")
 def test_direction_without_plan_is_no_trade(self):
  self.assertEqual(propose_action({},thesis="bullish")["action"],"NO_TRADE")
if __name__=="__main__":unittest.main()
