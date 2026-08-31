import unittest
from app.market_news_reaction_windows import build_reaction_window


class NoPreEventBoundaryTests(unittest.TestCase):
    def test_only_event_timestamp_candle_is_not_pre_event(self):
        event={"available_at":"2026-08-07T10:05:00+05:30","stance":"BULLISH"}
        candles=[{"timestamp":"2026-08-07T10:05:00+05:30","close":100}]
        r=build_reaction_window(event,candles,as_of="2026-08-07T11:05:00+05:30")
        self.assertEqual(r["status"],"NO_PRE_EVENT_MARKET")
        self.assertIsNone(r["pre_event"])


if __name__=="__main__":unittest.main()
