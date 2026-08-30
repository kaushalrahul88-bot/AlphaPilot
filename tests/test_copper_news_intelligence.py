import unittest
from app.copper_news_intelligence import assess_copper_news, apply_news_intelligence

def row(title):
    return {"available_at":"2026-08-10T10:00:00+00:00","source":"example.com","value":{"headline":title}}

class CopperNewsIntelligenceTests(unittest.TestCase):
    def test_person_named_copper_is_blocked(self):
        x=assess_copper_news(row("Kahleah Copper scores 31 as Mercury beat Sky"))
        self.assertEqual(x["disposition"],"BLOCK")

    def test_price_recap_is_context_only(self):
        x=assess_copper_news(row("BHP profit rises on record-high copper prices"))
        self.assertEqual(x["disposition"],"CONTEXT_ONLY")

    def test_causal_export_ban_is_allowed(self):
        x=assess_copper_news(row("Congo bans copper concentrate exports"))
        self.assertEqual(x["disposition"],"ALLOW")
        self.assertEqual(x["effect"],"BULLISH")

    def test_ambiguous_copper_story_cannot_vote(self):
        x=assess_copper_news(row("Copper outlook remains in focus"))
        self.assertNotEqual(x["disposition"],"ALLOW")

    def test_only_allowed_records_reach_directional_set(self):
        result=apply_news_intelligence([
            row("Congo bans copper concentrate exports"),
            row("Kahleah Copper scores 31 as Mercury beat Sky"),
            row("BHP profit rises on record-high copper prices"),
        ])
        self.assertEqual(result["counts"],{"ALLOW":1,"CONTEXT_ONLY":1,"BLOCK":1})
        self.assertEqual(len(result["allowed_records"]),1)

if __name__=="__main__":
    unittest.main()
