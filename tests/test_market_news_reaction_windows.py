import unittest
from app.market_news_reaction_windows import build_reaction_window
from app.market_news_reaction_engine import assess_market_news_reaction


class ReactionWindowTests(unittest.TestCase):
    def candles(self):
        return [{"timestamp":f"2026-08-07T10:{m:02d}:00+05:30","close":100+m/100,"volume":m}
                for m in range(0,61,5)]

    def test_selects_fixed_causal_horizons(self):
        w=build_reaction_window({"available_at":"2026-08-07T10:10:00+05:30","stance":"BULLISH"},self.candles())
        self.assertEqual(w["pre_event"]["timestamp"],"2026-08-07T10:10:00+05:30")
        self.assertEqual(w["immediate"]["timestamp"],"2026-08-07T10:15:00+05:30")
        self.assertEqual(w["confirmation"]["timestamp"],"2026-08-07T10:40:00+05:30")
        self.assertIsNone(w["assimilation"])

    def test_missing_pre_event_market_fails_closed(self):
        w=build_reaction_window({"available_at":"2026-08-07T09:00:00+05:30"},self.candles())
        self.assertEqual(w["status"],"NO_PRE_EVENT_MARKET")

    def test_window_feeds_reaction_engine_without_trade_outcomes(self):
        e={"available_at":"2026-08-07T10:00:00+05:30","stance":"BULLISH"}
        w=build_reaction_window(e,self.candles(),immediate_minutes=5,confirmation_minutes=10,assimilation_minutes=15)
        r=assess_market_news_reaction(e,w["pre_event"],w["immediate"],w["confirmation"],w["assimilation"],noise_floor=0.0001)
        self.assertTrue(r["outcome_blind"])
        self.assertIn(r["reaction_state"],{"ACCEPTED_REACTION","REVERSAL_AFTER_ACCEPTANCE"})

    def test_outcome_metadata_does_not_change_window(self):
        a={"available_at":"2026-08-07T10:10:00+05:30","stance":"BULLISH","outcome":"TARGET"}
        b={**a,"outcome":"STOP"}
        wa=build_reaction_window(a,self.candles());wb=build_reaction_window(b,self.candles())
        for key in ("pre_event","immediate","confirmation","assimilation"):
            self.assertEqual(wa[key],wb[key])


if __name__=="__main__":unittest.main()
