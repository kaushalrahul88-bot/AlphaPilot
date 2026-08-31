import unittest
from scripts.audit_market_news_reactions import audit


AS_OF="2026-08-07T11:05:00+05:30"


class MarketNewsReactionAuditTests(unittest.TestCase):
    def candles(self):
        return [{"timestamp":f"2026-08-07T10:{m:02d}:00+05:30","close":100+m/10,"volume":100+m,"open_interest":1000+m}
                for m in range(0,60,5)] + [{"timestamp":"2026-08-07T11:05:00+05:30","close":106.5,"volume":185,"open_interest":1065}]

    def news(self,outcome=None):
        r={"available_at":"2026-08-07T10:01:00+05:30","source":"Reuters","value":{"headline":"Supply disruption"},
           "news_intelligence":{"effect":"BULLISH","materiality":"HIGH","disposition":"ALLOW"}}
        if outcome is not None:r["outcome"]=outcome
        return {"records":[r]}

    def test_classifies_frozen_event(self):
        r=audit(self.news(),self.candles(),as_of=AS_OF)
        self.assertEqual(r["classified"],1);self.assertTrue(r["outcome_blind"]);self.assertFalse(r["outcomes_read"])
        self.assertEqual(r["coverage_counts"],{"CLASSIFIABLE":1})
        self.assertEqual(r["records"][0]["coverage_status"],"CLASSIFIABLE")

    def test_outcome_metadata_cannot_change_audit(self):
        a=audit(self.news("TARGET"),self.candles(),as_of=AS_OF);b=audit(self.news("STOP"),self.candles(),as_of=AS_OF)
        self.assertEqual(a["reaction_counts"],b["reaction_counts"])
        self.assertEqual(a["participation_counts"],b["participation_counts"])

    def test_missing_market_data_remains_unclassified(self):
        r=audit(self.news(),[],as_of=AS_OF)
        self.assertEqual(r["classified"],0);self.assertEqual(r["records"][0]["status"],"NO_PRE_EVENT_MARKET")
        self.assertEqual(r["market_coverage"]["status"],"NO_MARKET_DATA")
        self.assertEqual(r["records"][0]["coverage_status"],"INSUFFICIENT_REACTION_WINDOW")

    def test_event_after_frozen_candle_end_is_explicitly_outside_coverage(self):
        news={"records":[{"available_at":"2026-08-08T09:00:00+05:30","source":"Reuters",
                          "value":{"headline":"Later event"},
                          "news_intelligence":{"effect":"BULLISH","materiality":"HIGH","disposition":"ALLOW"}}]}
        r=audit(news,self.candles(),as_of="2026-08-08T12:00:00+05:30")
        self.assertEqual(r["classified"],0)
        self.assertEqual(r["coverage_counts"],{"OUTSIDE_CANDLE_COVERAGE":1})
        self.assertEqual(r["records"][0]["status"],"OUTSIDE_CANDLE_COVERAGE")
        self.assertNotIn("reaction",r["records"][0])

    def test_partial_horizon_is_separate_from_outside_coverage(self):
        news={"records":[{"available_at":"2026-08-07T10:50:00+05:30","source":"Reuters",
                          "value":{"headline":"Late in sample"},
                          "news_intelligence":{"effect":"BULLISH","materiality":"HIGH","disposition":"ALLOW"}}]}
        r=audit(news,self.candles(),as_of=AS_OF)
        self.assertEqual(r["classified"],0)
        self.assertEqual(r["records"][0]["status"],"PARTIAL")
        self.assertEqual(r["records"][0]["coverage_status"],"INSUFFICIENT_REACTION_WINDOW")


if __name__=="__main__":unittest.main()
