import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowNegativeLatenessTests(unittest.TestCase):
    def test_negative_lateness_is_rejected(self):
        with self.assertRaises(ValueError):
            build_reaction_window({"available_at":"2026-08-07T10:00:00+05:30"},[],
                                  as_of="2026-08-07T11:00:00+05:30",max_lateness_minutes=-1)


if __name__=="__main__":unittest.main()
