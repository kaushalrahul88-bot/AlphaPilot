import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowLatenessBoundaryTests(unittest.TestCase):
    def test_observation_at_tolerance_boundary_is_allowed(self):
        event={"available_at":"2026-08-07T10:02:00+05:30","stance":"BULLISH"}
        candles=[{"timestamp":"2026-08-07T10:00:00+05:30","close":100},
                 {"timestamp":"2026-08-07T10:12:00+05:30","close":101},
                 {"timestamp":"2026-08-07T10:37:00+05:30","close":102},
                 {"timestamp":"2026-08-07T11:07:00+05:30","close":103}]
        r=build_reaction_window(event,candles,as_of="2026-08-07T11:07:00+05:30",max_lateness_minutes=5)
        self.assertEqual(r["status"],"READY")


if __name__=="__main__":unittest.main()
