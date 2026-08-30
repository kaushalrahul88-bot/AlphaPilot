import unittest
from app.copper_historical_news_integrity_audit import audit_historical_news_records

class HistoricalNewsAuditContractTests(unittest.TestCase):
    def row(self,title,ts="2026-08-10T10:00:00+00:00"):
        return {"available_at":ts,"source":"example.com","value":{"headline":title,"language":"English","sentiment":"BULLISH","url":"https://example.com/x"}}

    def test_uncertain_headline_has_zero_directional_vote(self):
        result=audit_historical_news_records([self.row("Copper outlook: what happened last week")])
        self.assertEqual(result["classification_counts"].get("UNCERTAIN"),1)
        self.assertEqual(result["accepted_record_count"],0)

    def test_market_headline_is_accepted(self):
        result=audit_historical_news_records([self.row("Copper prices rise as LME inventories fall")])
        self.assertEqual(result["accepted_record_count"],1)

if __name__=="__main__":
    unittest.main()
