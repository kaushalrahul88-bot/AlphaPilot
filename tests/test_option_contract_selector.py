import unittest
from app.option_contract_selector import *
class SelectorTests(unittest.TestCase):
 def test_tighter_liquid_contract_wins(self):
  xs=[{"strike":1400,"ltp":20,"bid":19.8,"ask":20.2,"volume":1000,"oi":5000},{"strike":1420,"ltp":10,"bid":8,"ask":12,"volume":5,"oi":10}]
  r=select_option(xs,underlying_price=1398);self.assertEqual(r["contract"]["strike"],1400)
 def test_no_synthetic_contract(self):self.assertEqual(select_option([],underlying_price=1400)["status"],"NO_TRADE")
if __name__=="__main__":unittest.main()
