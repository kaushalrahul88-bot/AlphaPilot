import unittest
from scripts.audit_market_news_reactions import audit


class PartialReactionAuditTests(unittest.TestCase):
    def test_partial_window_is_not_classified(self):
        news={"records":[{"available_at":"2026-08-07T10:02:00+05:30","news_intelligence":{"effect":"BULLISH"}}]}
        candles=[{"timestamp":"2026-08-07T10:00:00+05:30","close":100},
                 {"timestamp":"2026-08-07T10:07:00+05:30","close":101}]
        r=audit(news,candles,as_of="2026-08-07T10:07:00+05:30")
        self.assertEqual(r["classified"],0)
        self.assertEqual(r["records"][0]["status"],"PARTIAL")
        self.assertEqual(r["records"][0]["window"]["horizon_status"]["confirmation"],"NOT_YET_OBSERVABLE")


if __name__=="__main__":unittest.main()
