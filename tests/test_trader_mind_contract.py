import unittest
from app.trader_mind_contract import trader_mind_contract,validate_setup
class TraderMindContractTests(unittest.TestCase):
 def test_prediction_is_not_objective(self):self.assertFalse(trader_mind_contract()["prediction_is_objective"])
 def test_no_trade_is_valid(self):self.assertTrue(validate_setup({"action":"NO_TRADE"})["valid"])
 def test_trade_requires_plan(self):self.assertFalse(validate_setup({"action":"BUY_CE","thesis":"bullish"})["valid"])
 def test_complete_trade_is_valid(self):
  x={"action":"BUY_PE","thesis":"failed breakout","entry_trigger":"break support","invalidation":"reclaim high","target_or_exit_logic":"next demand zone","risk_reward_basis":"structure"}
  self.assertTrue(validate_setup(x)["valid"])
if __name__=="__main__":unittest.main()
