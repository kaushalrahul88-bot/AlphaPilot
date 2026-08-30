import unittest
from app.trade_thesis_contract import *
class ThesisTests(unittest.TestCase):
 def good(self):
  return build_trade_thesis(direction="BULLISH",thesis="retest continuation",entry_trigger="hold support",invalidation="lose support",target_logic="next supply",risk_reward_basis="favorable",contradictions=["extended"],evidence_quality="STRONG")
 def test_maps_bullish_only_after_complete_thesis(self):self.assertEqual(gate_trade_thesis(self.good())["action"],"BUY_CE")
 def test_confirmation_means_wait(self):self.assertEqual(gate_trade_thesis(self.good(),confirmation_pending=True)["action"],"WAIT")
 def test_missing_context_can_force_abstention(self):
  x=self.good();x["evidence_quality"]="MODERATE";self.assertEqual(gate_trade_thesis(x,material_context_missing=True)["action"],"NO_TRADE")
if __name__=="__main__":unittest.main()
