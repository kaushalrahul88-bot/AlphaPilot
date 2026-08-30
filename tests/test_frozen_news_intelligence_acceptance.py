import unittest
from app.copper_news_intelligence import assess_copper_news, apply_news_intelligence

def row(title, when):
    return {"available_at":when,"source":"frozen-audit","value":{"headline":title}}

class FrozenAcceptedNewsIntelligenceTests(unittest.TestCase):
    def test_actual_frozen_integrity_keep_set_has_only_one_directional_vote(self):
        congo=row("Congo Bans Copper and Cobalt Concentrate Exports","2026-08-08T04:15:00+00:00")
        bhp=row("BHP Annual Profit Rises On Record - High Copper Prices","2026-08-18T04:45:00+00:00")
        c=assess_copper_news(congo)
        b=assess_copper_news(bhp)
        self.assertEqual(c["disposition"],"ALLOW")
        self.assertEqual(c["effect"],"BULLISH")
        self.assertEqual(c["transmission_mechanism"],"SUPPLY")
        self.assertIn(b["disposition"],("CONTEXT_ONLY","BLOCK"))
        self.assertNotEqual(b["disposition"],"ALLOW")
        self.assertIn("record-high copper prices",b["headline"].lower())
        result=apply_news_intelligence([congo,bhp])
        self.assertEqual(result["counts"]["ALLOW"],1)
        self.assertEqual(result["counts"]["CONTEXT_ONLY"]+result["counts"]["BLOCK"],1)
        self.assertEqual(
            result["allowed_records"][0]["value"]["headline"],
            "Congo Bans Copper and Cobalt Concentrate Exports",
        )

if __name__=="__main__":
    unittest.main()
