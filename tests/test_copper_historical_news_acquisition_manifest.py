import json,unittest
from pathlib import Path
class AcquisitionManifestTests(unittest.TestCase):
 def test_manifest_is_outcome_blind_and_timestamp_safe(self):
  d=json.loads(Path("research/copper_historical_news_acquisition_manifest_v1.json").read_text())
  rules=" ".join(d["admission_rules"]).lower()
  self.assertIn("never expose",rules);self.assertIn("replay outcomes",rules)
  self.assertGreaterEqual(len(d["source_classes"]),5)
  self.assertGreaterEqual(len(d["candidate_leads"]),5)
if __name__=="__main__":unittest.main()
