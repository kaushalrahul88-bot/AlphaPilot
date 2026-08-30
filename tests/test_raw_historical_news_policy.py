import json,unittest
from pathlib import Path
class RawNewsPolicyTests(unittest.TestCase):
 def test_raw_batch_has_no_directional_labels(self):
  d=json.loads(Path("research/copper_raw_historical_news_batch_v3.json").read_text())
  for r in d["records"]:
   self.assertNotIn("directional_effect",r);self.assertNotIn("confidence",r);self.assertNotIn("transmission_channel",r)
 def test_date_only_uses_next_session(self):
  d=json.loads(Path("research/copper_raw_historical_news_batch_v3.json").read_text())
  for r in d["records"]:
   if r["timestamp_precision"]=="DATE_ONLY":self.assertEqual(r["availability_policy"],"NEXT_TRADING_SESSION_OPEN")
if __name__=="__main__":unittest.main()
