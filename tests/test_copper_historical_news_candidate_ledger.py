import json,unittest
from pathlib import Path
class CandidateLedgerTests(unittest.TestCase):
 def test_candidates_are_quarantined_and_timestamped(self):
  d=json.loads(Path("research/copper_historical_news_candidate_ledger_v1.json").read_text())
  self.assertEqual(d["status"],"QUARANTINED_NOT_MARKET_BRAIN_INPUT")
  self.assertGreaterEqual(len(d["candidates"]),5)
  for x in d["candidates"]:
   self.assertTrue(x["published_at"]);self.assertTrue(x["source"]);self.assertTrue(x["url"]);self.assertTrue(x["fact"])
 def test_no_candidate_is_preapproved(self):
  d=json.loads(Path("research/copper_historical_news_candidate_ledger_v1.json").read_text())
  self.assertTrue(all(x["initial_label"].startswith(("REVIEW_","OUTSIDE_")) for x in d["candidates"]))
if __name__=="__main__":unittest.main()
