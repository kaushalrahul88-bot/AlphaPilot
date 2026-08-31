import unittest
from scripts.audit_market_news_reactions import audit


AS_OF="2026-08-07T11:05:00+05:30"


class MarketNewsReactionAuditTests(unittest.TestCase):
    def candles(self):
        return [{"timestamp":f"2026-08-07T10:{m:02d}:00+05:30","close":100+m/10,"volume":100+m,"open_interest":1000+m}
                for m in range(0,60,5)] + [{"timestamp":"2026-08-07T11:05:00+05:30","close":106.5,"volume":185,"open_interest":1065}]

    def news(self,outcome=None,effect="BULLISH"):
        r={"available_at":"2026-08-07T10:01:00+05:30","source":"Reuters","value":{"headline":"Supply disruption"},
           "news_intelligence":{"effect":effect,"materiality":"HIGH","disposition":"ALLOW"}}
        if outcome is not None:r["outcome"]=outcome
        return {"records":[r]}

    def test_classifies_frozen_event(self):
        r=audit(self.news(),self.candles(),as_of=AS_OF)
        self.assertEqual(r["classified"],1);self.assertTrue(r["outcome_blind"]);self.assertFalse(r["outcomes_read"])
        self.assertEqual(r["coverage_counts"],{"CLASSIFIABLE":1})
        self.assertEqual(sum(r["observed_path_counts"].values()),1)
        self.assertEqual(sum(r["assimilation_counts"].values()),1)
        self.assertEqual(sum(r["path_materiality_counts"].values()),1)
        self.assertTrue(r["records"][0]["assimilation"]["shadow_only"])
        self.assertTrue(r["records"][0]["path_materiality"]["shadow_only"])
        self.assertTrue(r["records"][0]["path_materiality"]["classification_unchanged"])

    def test_unknown_news_stance_does_not_erase_observed_market_path_or_shadows(self):
        r=audit(self.news(effect="UNKNOWN"),self.candles(),as_of=AS_OF)
        row=r["records"][0]
        self.assertEqual(row["reaction"]["reaction_state"],"UNOBSERVED")
        self.assertEqual(row["observed_path"]["observation_status"],"OBSERVED")
        self.assertNotEqual(row["observed_path"]["path_state"],"UNOBSERVED")
        self.assertNotEqual(row["assimilation"]["assimilation_state"],"UNOBSERVED")
        self.assertNotEqual(row["path_materiality"]["materiality_state"],"UNOBSERVED")
        self.assertEqual(sum(r["observed_path_counts"].values()),1)
        self.assertEqual(sum(r["assimilation_counts"].values()),1)
        self.assertEqual(sum(r["path_materiality_counts"].values()),1)

    def test_outcome_metadata_cannot_change_audit(self):
        a=audit(self.news("TARGET"),self.candles(),as_of=AS_OF);b=audit(self.news("STOP"),self.candles(),as_of=AS_OF)
        self.assertEqual(a["reaction_counts"],b["reaction_counts"]);self.assertEqual(a["coverage_counts"],b["coverage_counts"])
        self.assertEqual(a["observed_path_counts"],b["observed_path_counts"])
        self.assertEqual(a["assimilation_counts"],b["assimilation_counts"])
        self.assertEqual(a["path_materiality_counts"],b["path_materiality_counts"])
        self.assertEqual(a["records"][0]["assimilation"],b["records"][0]["assimilation"])
        self.assertEqual(a["records"][0]["path_materiality"],b["records"][0]["path_materiality"])

    def test_missing_market_data_remains_unclassified(self):
        r=audit(self.news(),[],as_of=AS_OF)
        self.assertEqual(r["classified"],0);self.assertEqual(r["records"][0]["status"],"NO_PRE_EVENT_MARKET")
        self.assertEqual(r["market_coverage"]["status"],"NO_MARKET_DATA")
        self.assertEqual(r["observed_path_counts"],{})
        self.assertEqual(r["assimilation_counts"],{})
        self.assertEqual(r["path_materiality_counts"],{})

    def test_event_after_frozen_candle_end_is_outside_coverage(self):
        news={"records":[{"available_at":"2026-08-31T09:00:00+05:30","source":"Reuters",
                          "value":{"headline":"Later event"},
                          "news_intelligence":{"effect":"BULLISH","materiality":"HIGH","disposition":"ALLOW"}}]}
        r=audit(news,self.candles(),as_of="2026-08-31T12:00:00+05:30")
        self.assertEqual(r["classified"],0);self.assertEqual(r["coverage_counts"],{"OUTSIDE_CANDLE_COVERAGE":1})
        self.assertEqual(r["records"][0]["status"],"OUTSIDE_CANDLE_COVERAGE")
        self.assertNotIn("reaction",r["records"][0]);self.assertNotIn("observed_path",r["records"][0])
        self.assertNotIn("assimilation",r["records"][0]);self.assertNotIn("path_materiality",r["records"][0])

    def test_partial_horizon_is_not_outside_coverage(self):
        news={"records":[{"available_at":"2026-08-07T10:50:00+05:30","source":"Reuters",
                          "value":{"headline":"Late in sample"},
                          "news_intelligence":{"effect":"BULLISH","materiality":"HIGH","disposition":"ALLOW"}}]}
        r=audit(news,self.candles(),as_of=AS_OF)
        self.assertEqual(r["classified"],0);self.assertEqual(r["records"][0]["status"],"PARTIAL")
        self.assertEqual(r["records"][0]["coverage_status"],"INSUFFICIENT_REACTION_WINDOW")
        self.assertNotIn("observed_path",r["records"][0]);self.assertNotIn("assimilation",r["records"][0])
        self.assertNotIn("path_materiality",r["records"][0])


if __name__=="__main__":unittest.main()
