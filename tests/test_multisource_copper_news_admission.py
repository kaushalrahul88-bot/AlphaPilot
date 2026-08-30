import json,unittest
from pathlib import Path
from scripts.audit_multisource_copper_news_admission import record
class AdmissionAuditTests(unittest.TestCase):
 def test_candidate_conversion_preserves_timestamp_and_provenance(self):
  d=json.loads(Path("research/copper_historical_news_candidate_ledger_v1.json").read_text())
  x=d["candidates"][0];r=record(x)
  self.assertEqual(r["available_at"],x["published_at"]);self.assertEqual(r["source"],x["source"])
  self.assertEqual(r["value"]["url"],x["url"]);self.assertEqual(r["quality"],"MULTISOURCE_CANDIDATE")
if __name__=="__main__":unittest.main()
