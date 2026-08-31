import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowOutcomeBlindTests(unittest.TestCase):
    def test_outcome_field_does_not_change_window(self):
        candles=[{"timestamp":"2026-08-07T10:00:00+05:30","close":100},
                 {"timestamp":"2026-08-07T10:10:00+05:30","close":101},
                 {"timestamp":"2026-08-07T10:35:00+05:30","close":102},
                 {"timestamp":"2026-08-07T11:05:00+05:30","close":103}]
        base={"available_at":"2026-08-07T10:02:00+05:30","stance":"BULLISH"}
        a=build_reaction_window({**base,"outcome":"TARGET"},candles,as_of="2026-08-07T11:05:00+05:30")
        b=build_reaction_window({**base,"outcome":"STOP"},candles,as_of="2026-08-07T11:05:00+05:30")
        a["event"].pop("outcome",None);b["event"].pop("outcome",None)
        self.assertEqual(a,b)


if __name__=="__main__":unittest.main()
