import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowSelectionTests(unittest.TestCase):
    def test_complete_window_selects_bounded_observations(self):
        event={"available_at":"2026-08-07T10:02:00+05:30","stance":"BULLISH"}
        candles=[
            {"timestamp":"2026-08-07T10:00:00+05:30","close":100},
            {"timestamp":"2026-08-07T10:10:00+05:30","close":101},
            {"timestamp":"2026-08-07T10:35:00+05:30","close":102},
            {"timestamp":"2026-08-07T11:05:00+05:30","close":103},
        ]
        r=build_reaction_window(event,candles,as_of="2026-08-07T11:05:00+05:30")
        self.assertEqual(r["status"],"READY")
        self.assertEqual(r["pre_event"]["price"],100)
        self.assertEqual(r["immediate"]["price"],101)
        self.assertEqual(r["confirmation"]["price"],102)
        self.assertEqual(r["assimilation"]["price"],103)


if __name__=="__main__":unittest.main()
