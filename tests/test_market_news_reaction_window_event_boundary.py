import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowEventBoundaryTests(unittest.TestCase):
    def test_exact_event_timestamp_is_not_pre_event(self):
        event={"available_at":"2026-08-07T10:05:00+05:30","stance":"BULLISH"}
        candles=[{"timestamp":"2026-08-07T10:00:00+05:30","close":100},
                 {"timestamp":"2026-08-07T10:05:00+05:30","close":110},
                 {"timestamp":"2026-08-07T10:10:00+05:30","close":111},
                 {"timestamp":"2026-08-07T10:35:00+05:30","close":112},
                 {"timestamp":"2026-08-07T11:05:00+05:30","close":113}]
        r=build_reaction_window(event,candles,as_of="2026-08-07T11:05:00+05:30")
        self.assertEqual(r["pre_event"]["timestamp"],"2026-08-07T10:00:00+05:30")
        self.assertEqual(r["pre_event"]["price"],100)


if __name__=="__main__":unittest.main()
