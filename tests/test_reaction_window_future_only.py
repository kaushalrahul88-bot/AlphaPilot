import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowFutureOnlyTests(unittest.TestCase):
    def test_future_only_data_has_no_pre_event_market(self):
        event={"available_at":"2026-08-07T10:02:00+05:30"}
        candles=[{"timestamp":"2026-08-07T10:07:00+05:30","close":101}]
        r=build_reaction_window(event,candles,as_of="2026-08-07T11:02:00+05:30")
        self.assertEqual(r["status"],"NO_PRE_EVENT_MARKET")


if __name__=="__main__":unittest.main()
