import unittest
from app.option_expression_gate import option_expression_gate
class OptionGateTests(unittest.TestCase):
 def test_missing_option_data_does_not_fabricate(self):
  r=option_expression_gate({"direction":"BULLISH"},None)
  self.assertEqual(r["action"],"UNDERLYING_SETUP_ONLY")
 def test_direction_maps_to_option_side(self):
  r=option_expression_gate({"direction":"BEARISH"},{"contracts":[{"option_type":"PE","ltp":20,"bid":19.5,"ask":20.5}]})
  self.assertEqual(r["option_side"],"PE")
 def test_bad_spread_rejected(self):
  r=option_expression_gate({"direction":"BULLISH"},{"contracts":[{"option_type":"CE","ltp":20,"bid":18,"ask":22}]})
  self.assertEqual(r["action"],"NO_TRADE")
if __name__=="__main__":unittest.main()
