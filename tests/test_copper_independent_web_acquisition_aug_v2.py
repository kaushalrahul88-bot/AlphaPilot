import json,unittest
from pathlib import Path
from scripts.admit_copper_causal_events import classify
class IndependentWebAcquisitionTests(unittest.TestCase):
 def setUp(self):self.d=json.loads(Path("research/copper_independent_web_acquisition_aug_v2.json").read_text())
 def test_all_events_quarantined(self):self.assertEqual(self.d["status"],"QUARANTINED_NOT_MARKET_BRAIN_INPUT")
 def test_date_only_reuters_events_fail_closed(self):
  for x in self.d["events"]:
   if x["provenance_status"]=="CANDIDATE_TIMESTAMP_DATE_ONLY":self.assertEqual(classify(x)[0],"BLOCK")
 def test_mixed_verified_aug4_is_context_only(self):self.assertEqual(classify(self.d["events"][0])[0],"CONTEXT_ONLY")
if __name__=="__main__":unittest.main()
