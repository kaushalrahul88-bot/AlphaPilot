import unittest
from app.market_regime_observer import observe_regime
class RegimeTests(unittest.TestCase):
 def test_extended_trend_does_not_say_buy(self):
  r=observe_regime({"trend_structure":"UPTREND","location":"EXTENDED_ABOVE_VALUE"})
  self.assertIn("EXTENDED",r["regime_labels"]);self.assertNotIn("BUY_CE",str(r))
 def test_range_discourages_middle_chasing(self):
  r=observe_regime({"trend_structure":"RANGE"})
  self.assertTrue(any("middle" in x for x in r["strategy_implications"]))
if __name__=="__main__":unittest.main()
