import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowPublishedAtTests(unittest.TestCase):
    def test_published_at_is_supported_when_available_at_missing(self):
        event={"published_at":"2026-08-07T10:02:00+05:30","stance":"BULLISH"}
        candles=[{"timestamp":"2026-08-07T10:00:00+05:30","close":100},
                 {"timestamp":"2026-08-07T10:07:00+05:30","close":101},
                 {"timestamp":"2026-08-07T10:32:00+05:30","close":102},
                 {"timestamp":"2026-08-07T11:02:00+05:30","close":103}]
        r=build_reaction_window(event,candles,as_of="2026-08-07T11:02:00+05:30")
        self.assertEqual(r["status"],"READY")


if __name__=="__main__":unittest.main()
