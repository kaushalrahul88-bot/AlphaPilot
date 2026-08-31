import unittest

from app.market_news_reaction_windows import build_reaction_window


class MarketNewsReactionPitTests(unittest.TestCase):
    EVENT={"available_at":"2026-08-07T10:02:00+05:30","stance":"BULLISH"}

    def candle(self,minute,price=100,oi=1000):
        return {"timestamp":f"2026-08-07T10:{minute:02d}:00+05:30","close":price,"volume":100,"open_interest":oi}

    def test_future_candles_after_as_of_are_invisible(self):
        candles=[self.candle(0),self.candle(7,101),self.candle(32,102),self.candle(40,999)]
        a=build_reaction_window(self.EVENT,candles,as_of="2026-08-07T10:32:00+05:30",assimilation_minutes=30)
        b=build_reaction_window(self.EVENT,candles+[self.candle(59,1)],as_of="2026-08-07T10:32:00+05:30",assimilation_minutes=30)
        self.assertEqual(a,b)

    def test_event_after_as_of_fails_closed(self):
        r=build_reaction_window(self.EVENT,[self.candle(0)],as_of="2026-08-07T10:01:00+05:30")
        self.assertEqual(r["status"],"EVENT_NOT_YET_AVAILABLE")

    def test_horizon_not_yet_observable_is_not_filled(self):
        r=build_reaction_window(self.EVENT,[self.candle(0),self.candle(7,101)],as_of="2026-08-07T10:07:00+05:30")
        self.assertEqual(r["horizon_status"]["immediate"],"OBSERVED")
        self.assertEqual(r["horizon_status"]["confirmation"],"NOT_YET_OBSERVABLE")
        self.assertIsNone(r["confirmation"])

    def test_late_next_session_style_observation_is_rejected(self):
        candles=[self.candle(0),{"timestamp":"2026-08-07T11:00:00+05:30","close":101,"volume":100,"open_interest":1000}]
        r=build_reaction_window(self.EVENT,candles,as_of="2026-08-07T11:10:00+05:30")
        self.assertEqual(r["horizon_status"]["immediate"],"NO_OBSERVATION_WITHIN_TOLERANCE")
        self.assertIsNone(r["immediate"])

    def test_zero_open_interest_is_preserved(self):
        r=build_reaction_window(self.EVENT,[self.candle(0,oi=0),self.candle(7,101,0),self.candle(32,102,0),
                                            {"timestamp":"2026-08-07T11:02:00+05:30","close":103,"volume":100,"open_interest":0}],
                                as_of="2026-08-07T11:02:00+05:30")
        self.assertEqual(r["pre_event"]["open_interest"],0)
        self.assertEqual(r["immediate"]["open_interest"],0)

    def test_invalid_horizon_order_is_rejected(self):
        with self.assertRaises(ValueError):
            build_reaction_window(self.EVENT,[self.candle(0)],as_of="2026-08-07T11:00:00+05:30",
                                  immediate_minutes=30,confirmation_minutes=5)


if __name__=="__main__":unittest.main()
