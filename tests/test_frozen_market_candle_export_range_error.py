import unittest
from app.frozen_market_candle_export import build_frozen_candle_artifact


class FrozenExportRangeErrorTests(unittest.TestCase):
    def test_end_before_start_is_rejected(self):
        with self.assertRaises(ValueError):
            build_frozen_candle_artifact([],symbol="COPPER",trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                                         start="2026-08-08T10:00:00+05:30",end="2026-08-07T10:00:00+05:30",
                                         exported_at="2026-08-31T12:00:00+00:00")


if __name__=="__main__":unittest.main()
