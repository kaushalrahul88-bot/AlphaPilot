import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionHorizonStatusTests(unittest.TestCase):
    def test_ready_window_marks_all_horizons_observed(self):
        event={"available_at":"2026-08-07T10:02:00+05:30"}
        candles=[{"timestamp":"2026-08-07T10:00:00+05:30","close":100},
                 {"timestamp":"2026-08-07T10:07:00+05:30","close":101},
                 {"timestamp":"2026-08-07T10:32:00+05:30","close":102},
                 {"timestamp":"2026-08-07T11:02:00+05:30","close":103}]
        r=build_reaction_window(event,candles,as_of="2026-08-07T11:02:00+05:30")
        self.assertEqual(set(r["horizon_status"].values()),{"OBSERVED"})


if __name__=="__main__":unittest.main()
