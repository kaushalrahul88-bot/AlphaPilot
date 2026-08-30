import unittest
from app.copper_historical_news import _relevant
from app.copper_historical_news_integrity_audit import audit_historical_news_records
from app.copper_news_intelligence import assess_copper_news

def row(title):
    return {"available_at":"2026-08-10T10:00:00+00:00","source":"example.com",
            "value":{"headline":title,"language":"English","sentiment":"BULLISH"}}

class HistoricalNewsCoverageExpansionTests(unittest.TestCase):
    def test_retrieval_keeps_named_copper_asset_without_copper_word(self):
        self.assertTrue(_relevant("Strike halts output at Escondida mine"))

    def test_integrity_accepts_named_asset_supply_disruption(self):
        d=audit_historical_news_records([row("Strike disrupts output at Escondida mine")])
        self.assertEqual(d["accepted_record_count"],1)

    def test_news_intelligence_allows_named_asset_supply_disruption(self):
        d=assess_copper_news(row("Strike disrupts output at Escondida mine"))
        self.assertEqual(d["disposition"],"ALLOW")
        self.assertEqual(d["effect"],"BULLISH")
        self.assertEqual(d["transmission_mechanism"],"SUPPLY")

    def test_unrelated_company_without_copper_or_named_asset_stays_blocked(self):
        d=assess_copper_news(row("BHP iron ore project reports higher output"))
        self.assertEqual(d["disposition"],"BLOCK")

if __name__=="__main__":
    unittest.main()
