import unittest

from scripts.audit_market_news_reactions import audit


class ReactionAuditCoverageGuardRegressionTests(unittest.TestCase):
    def test_mixed_covered_and_future_events_do_not_pollute_reaction_counts(self):
        candles=[
            {"timestamp":f"2026-08-07T10:{minute:02d}:00+05:30","close":100+minute/10,
             "volume":100+minute,"open_interest":1000+minute}
            for minute in range(0,60,5)
        ] + [{"timestamp":"2026-08-07T11:05:00+05:30","close":106.5,"volume":185,"open_interest":1065}]
        news={"records":[
            {"available_at":"2026-08-07T10:01:00+05:30","source":"Reuters",
             "value":{"headline":"Covered event"},
             "news_intelligence":{"effect":"BULLISH","materiality":"HIGH","disposition":"ALLOW"}},
            {"available_at":"2026-08-31T09:00:00+05:30","source":"Reuters",
             "value":{"headline":"Outside frozen candle range"},
             "news_intelligence":{"effect":"BULLISH","materiality":"HIGH","disposition":"ALLOW"}},
        ]}
        result=audit(news,candles,as_of="2026-08-31T12:00:00+05:30")
        self.assertEqual(result["events"],2)
        self.assertEqual(result["classified"],1)
        self.assertEqual(result["coverage_counts"],{"CLASSIFIABLE":1,"OUTSIDE_CANDLE_COVERAGE":1})
        self.assertEqual(sum(result["reaction_counts"].values()),1)
        self.assertEqual(result["records"][1]["status"],"OUTSIDE_CANDLE_COVERAGE")
        self.assertNotIn("reaction",result["records"][1])
        self.assertNotIn("participation",result["records"][1])


if __name__=="__main__":unittest.main()
