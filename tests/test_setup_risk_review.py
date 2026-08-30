import unittest
from app.setup_risk_review import review_setup_risk
class RiskTests(unittest.TestCase):
 def test_bad_rr_rejected(self):
  r=review_setup_risk({"direction":"BULLISH","entry_price":100,"stop_price":98,"target_price":102},{})
  self.assertEqual(r["status"],"NO_TRADE")
 def test_valid_structure_passes(self):
  r=review_setup_risk({"direction":"BEARISH","entry_price":100,"stop_price":102,"target_price":96},{})
  self.assertEqual(r["status"],"PASS_TO_OPTION_BRAIN")
if __name__=="__main__":unittest.main()
