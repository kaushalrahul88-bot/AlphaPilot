import unittest
from app.frozen_market_candle_export import build_frozen_candle_artifact


class FrozenExportBadRowTests(unittest.TestCase):
    def test_short_sequence_row_is_rejected(self):
        with self.assertRaises(ValueError):
            build_frozen_candle_artifact([["2026-08-07T10:00:00+05:30",1]],symbol="COPPER",
                                         trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                                         start="2026-08-07T10:00:00+05:30",end="2026-08-07T10:00:00+05:30",
                                         exported_at="2026-08-31T12:00:00+00:00")


if __name__=="__main__":unittest.main()
