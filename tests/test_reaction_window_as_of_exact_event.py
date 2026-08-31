import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowAsOfEventTests(unittest.TestCase):
    def test_as_of_equal_event_exposes_only_pre_event_market(self):
        ts="2026-08-07T10:05:00+05:30"
        r=build_reaction_window({"available_at":ts},[{"timestamp":"2026-08-07T10:00:00+05:30","close":100}],as_of=ts)
        self.assertEqual(r["status"],"PARTIAL")
        self.assertIsNotNone(r["pre_event"])
        self.assertEqual(set(r["horizon_status"].values()),{"NOT_YET_OBSERVABLE"})


if __name__=="__main__":unittest.main()
