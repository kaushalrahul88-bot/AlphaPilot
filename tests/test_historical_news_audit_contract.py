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
        result=audit_historical_news_records([self.row("LME copper inventories fall sharply, tightening available supply")])
        self.assertEqual(result["accepted_record_count"],1)

if __name__=="__main__":
    unittest.main()

    def test_sports_name_copper_is_rejected(self):
        result=audit_historical_news_records([self.row("Kahleah Copper scores 31 as Mercury beat Sky")])
        self.assertEqual(result["classification_counts"].get("REJECT"),1)

    def test_copper_theft_is_rejected(self):
        result=audit_historical_news_records([self.row("Two arrested for stealing copper wire")])
        self.assertEqual(result["classification_counts"].get("REJECT"),1)

    def test_company_equity_story_is_rejected(self):
        result=audit_historical_news_records([self.row("Hindustan Copper shares rise as OFS opens")])
        self.assertEqual(result["classification_counts"].get("REJECT"),1)

    def test_small_exploration_story_is_rejected(self):
        result=audit_historical_news_records([self.row("Junior Copper project begins drilling program")])
        self.assertEqual(result["classification_counts"].get("REJECT"),1)

    def test_major_supply_disruption_can_be_kept(self):
        result=audit_historical_news_records([self.row("Strike disrupts copper output at BHP Escondida mine")])
        self.assertEqual(result["accepted_record_count"],1)

    def test_inventory_price_story_is_kept(self):
        result=audit_historical_news_records([self.row("LME copper inventories fall sharply, tightening available supply")])
        self.assertEqual(result["accepted_record_count"],1)

    def test_price_recap_does_not_vote(self):
        result=audit_historical_news_records([self.row("BHP annual profit rises on record-high copper prices")])
        self.assertEqual(result["accepted_record_count"],0)
        self.assertEqual(result["classification_counts"].get("UNCERTAIN"),1)

    def test_price_surge_consequence_does_not_vote(self):
        result=audit_historical_news_records([self.row("Copper price surge lifts global mining behemoth")])
        self.assertEqual(result["accepted_record_count"],0)
        self.assertEqual(result["classification_counts"].get("UNCERTAIN"),1)

    def test_promotional_project_demand_claim_is_rejected(self):
        result=audit_historical_news_records([self.row("Solis Minerals targets rising lithium and copper demand with Brazil and Peru drilling")])
        self.assertEqual(result["accepted_record_count"],0)
        self.assertEqual(result["classification_counts"].get("REJECT"),1)

    def test_causal_export_ban_can_vote(self):
        result=audit_historical_news_records([self.row("Congo bans copper and cobalt concentrate exports")])
        self.assertEqual(result["accepted_record_count"],1)
