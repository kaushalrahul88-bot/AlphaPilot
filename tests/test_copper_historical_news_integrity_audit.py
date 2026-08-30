import unittest
from app.copper_historical_news_integrity_audit import audit_historical_news_records
class HistoricalNewsIntegrityTests(unittest.TestCase):
 def row(self,title,ts="2026-08-10T10:00:00+00:00"):
  return {"available_at":ts,"source":"example.com","value":{"headline":title,"language":"English","sentiment":"BULLISH","url":"https://example.com/x"}}
 def test_irrelevant_copper_theft_rejected(self):
  r=audit_historical_news_records([self.row("Police arrest suspects in copper theft")])
  self.assertEqual(r["classification_counts"].get("REJECT"),1)
 def test_market_copper_kept(self):
  r=audit_historical_news_records([self.row("Copper prices rise as LME inventories fall")])
  self.assertEqual(r["classification_counts"].get("KEEP"),1)
 def test_duplicate_cannot_vote_twice(self):
  rows=[self.row("Copper prices rise as LME inventories fall"),self.row("Copper prices rise as LME inventories fall","2026-08-10T10:05:00+00:00")]
  r=audit_historical_news_records(rows)
  self.assertEqual(r["classification_counts"].get("DUPLICATE"),1)
if __name__=="__main__":unittest.main()
