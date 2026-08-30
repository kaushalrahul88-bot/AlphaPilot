import unittest
from scripts.admit_copper_causal_events import classify
class CausalEventAdmissionTests(unittest.TestCase):
 def base(self):return {"available_at":"2026-08-10T09:00:00+05:30","provenance_status":"VERIFIED","transmission_channel":"SUPPLY_DOWN","directional_effect":"BULLISH","confidence":0.9}
 def test_verified_explicit_effect_allowed(self):self.assertEqual(classify(self.base())[0],"ALLOW")
 def test_mixed_stays_context(self):
  x=self.base();x["directional_effect"]="MIXED";self.assertEqual(classify(x)[0],"CONTEXT_ONLY")
 def test_unverified_is_blocked(self):
  x=self.base();x["provenance_status"]="CANDIDATE";self.assertEqual(classify(x)[0],"BLOCK")
 def test_low_confidence_is_context(self):
  x=self.base();x["confidence"]=0.6;self.assertEqual(classify(x)[0],"CONTEXT_ONLY")
if __name__=="__main__":unittest.main()
