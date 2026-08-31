import unittest

from scripts.audit_market_news_reactions import audit


class QualifiedPathAuditIntegrationTests(unittest.TestCase):
    def test_audit_exposes_shadow_without_changing_classification_count(self):
        candles=[
            {"timestamp":f"2026-08-07T10:{minute:02d}:00+05:30","close":100+minute/10,"volume":100+minute,"open_interest":1000+minute}
            for minute in range(0,60,5)
        ] + [{"timestamp":"2026-08-07T11:05:00+05:30","close":106.5,"volume":185,"open_interest":1065}]
        news={"records":[{
            "available_at":"2026-08-07T10:01:00+05:30",
            "source":"Reuters",
            "value":{"headline":"Supply disruption"},
            "news_intelligence":{"effect":"UNKNOWN","materiality":"HIGH","disposition":"CONTEXT_ONLY"},
        }]}
        result=audit(news,candles,as_of="2026-08-07T11:05:00+05:30")
        self.assertEqual(result["classified"],1)
        self.assertEqual(sum(result["observed_path_counts"].values()),1)
        self.assertEqual(sum(result["materiality_qualified_path_counts"].values()),1)
        row=result["records"][0]
        shadow=row["materiality_qualified_path"]
        self.assertEqual(shadow["mode"],"MARKET_NEWS_MATERIALITY_QUALIFIED_PATH_SHADOW_V1")
        self.assertTrue(shadow["shadow_only"])
        self.assertTrue(shadow["classification_unchanged"])
        self.assertEqual(row["reaction"]["reaction_state"],"UNOBSERVED")
        self.assertNotEqual(row["observed_path"]["path_state"],"UNOBSERVED")

    def test_outside_coverage_does_not_receive_qualified_path(self):
        candles=[{"timestamp":"2026-08-07T10:00:00+05:30","close":100,"volume":100,"open_interest":1000}]
        news={"records":[{
            "available_at":"2026-08-31T09:00:00+05:30",
            "source":"Reuters",
            "value":{"headline":"Later event"},
            "news_intelligence":{"effect":"BULLISH","materiality":"HIGH","disposition":"ALLOW"},
        }]}
        result=audit(news,candles,as_of="2026-08-31T12:00:00+05:30")
        self.assertEqual(result["classified"],0)
        self.assertEqual(result["materiality_qualified_path_counts"],{})
        self.assertNotIn("materiality_qualified_path",result["records"][0])


if __name__=="__main__":unittest.main()
