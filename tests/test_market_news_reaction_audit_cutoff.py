import unittest
from scripts.audit_market_news_reactions import audit


class ReactionAuditCutoffTests(unittest.TestCase):
    def test_appending_future_market_data_cannot_change_as_of_audit(self):
        news={"records":[{"available_at":"2026-08-07T10:02:00+05:30","news_intelligence":{"effect":"BULLISH"}}]}
        visible=[{"timestamp":"2026-08-07T10:00:00+05:30","close":100,"volume":100,"open_interest":1000},
                 {"timestamp":"2026-08-07T10:07:00+05:30","close":101,"volume":130,"open_interest":1010}]
        future=[{"timestamp":"2026-08-07T10:32:00+05:30","close":50,"volume":9999,"open_interest":1},
                {"timestamp":"2026-08-07T11:02:00+05:30","close":10,"volume":9999,"open_interest":1}]
        cutoff="2026-08-07T10:07:00+05:30"
        self.assertEqual(audit(news,visible,as_of=cutoff),audit(news,visible+future,as_of=cutoff))


if __name__=="__main__":unittest.main()
