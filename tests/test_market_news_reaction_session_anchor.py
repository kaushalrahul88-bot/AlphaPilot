import unittest

from app.market_news_reaction_windows import build_reaction_window
from scripts.audit_market_news_reactions import audit


EVENT={"available_at":"2026-08-08T09:45:00+05:30","stance":"BULLISH"}
AS_OF="2026-08-10T10:00:00+05:30"
CANDLES=[
    {"timestamp":"2026-08-07T23:00:00+05:30","close":100.0,"volume":100,"open_interest":1000},
    {"timestamp":"2026-08-10T09:00:00+05:30","close":101.0,"volume":120,"open_interest":1005},
    {"timestamp":"2026-08-10T09:05:00+05:30","close":101.2,"volume":130,"open_interest":1008},
    {"timestamp":"2026-08-10T09:30:00+05:30","close":101.5,"volume":140,"open_interest":1012},
    {"timestamp":"2026-08-10T10:00:00+05:30","close":101.8,"volume":150,"open_interest":1015},
]


class MarketNewsReactionSessionAnchorTests(unittest.TestCase):
    def test_explicit_session_anchor_preserves_pre_news_close_and_uses_open_horizons(self):
        w=build_reaction_window(EVENT,CANDLES,as_of=AS_OF,reaction_anchor="2026-08-10T09:00:00+05:30")
        self.assertEqual(w["status"],"READY")
        self.assertEqual(w["pre_event"]["timestamp"],"2026-08-07T23:00:00+05:30")
        self.assertEqual(w["immediate"]["timestamp"],"2026-08-10T09:05:00+05:30")
        self.assertEqual(w["confirmation"]["timestamp"],"2026-08-10T09:30:00+05:30")
        self.assertEqual(w["assimilation"]["timestamp"],"2026-08-10T10:00:00+05:30")
        self.assertGreater(w["reaction_anchor_shift_minutes"],0)

    def test_anchor_before_event_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"cannot precede"):
            build_reaction_window(EVENT,CANDLES,as_of=AS_OF,reaction_anchor="2026-08-07T09:00:00+05:30")

    def test_copper_audit_automatically_anchors_weekend_event_to_next_mcx_open(self):
        news={"records":[{"available_at":EVENT["available_at"],"source":"Reuters",
                          "value":{"headline":"Weekend supply disruption"},
                          "news_intelligence":{"effect":"BULLISH","materiality":"HIGH","disposition":"ALLOW"}}]}
        result=audit(news,CANDLES,as_of=AS_OF)
        self.assertEqual(result["classified"],1)
        row=result["records"][0]
        self.assertEqual(row["coverage_status"],"CLASSIFIABLE")
        self.assertEqual(row["window"]["reaction_anchor_timestamp"],"2026-08-10T09:00:00+05:30")
        self.assertEqual(row["window"]["pre_event"]["timestamp"],"2026-08-07T23:00:00+05:30")
        self.assertTrue(result["outcome_blind"])
        self.assertFalse(result["outcomes_read"])


if __name__=="__main__":
    unittest.main()
