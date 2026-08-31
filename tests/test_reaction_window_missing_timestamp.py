import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowMissingTimestampTests(unittest.TestCase):
    def test_missing_event_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            build_reaction_window({"stance":"BULLISH"},[],as_of="2026-08-07T11:00:00+05:30")


if __name__=="__main__":unittest.main()
