import json,unittest
from pathlib import Path
class RawTimestampedNewsBatchTests(unittest.TestCase):
 def setUp(self):self.d=json.loads(Path("research/copper_raw_timestamped_news_batch_v1.json").read_text())
 def test_collector_has_no_directional_labels(self):
  forbidden={"directional_effect","effect_basis","confidence","disposition"}
  for r in self.d["records"]:self.assertFalse(forbidden.intersection(r))
 def test_date_only_records_fail_closed_by_contract(self):
  xs=[r for r in self.d["records"] if r.get("timestamp_precision")]
  self.assertTrue(xs)
  self.assertTrue(all(r["timestamp_precision"]=="DATE_ONLY_DO_NOT_EXPOSE_INTRADAY" for r in xs))
 def test_provenance_present(self):
  for r in self.d["records"]:
   self.assertTrue(r["available_at"]);self.assertTrue(r["source"]);self.assertTrue(r["source_url"]);self.assertTrue(r["headline"])
if __name__=="__main__":unittest.main()
