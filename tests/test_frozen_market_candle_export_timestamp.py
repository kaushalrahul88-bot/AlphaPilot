import unittest
from app.frozen_market_candle_export import build_frozen_candle_artifact


class FrozenExportTimestampTests(unittest.TestCase):
    def test_timestamp_is_canonical_iso(self):
        r=build_frozen_candle_artifact([["2026-08-07T10:00:00+05:30",1,1,1,1,1]],symbol="COPPER",
                                       trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                                       start="2026-08-07T10:00:00+05:30",end="2026-08-07T10:00:00+05:30",
                                       exported_at="2026-08-31T12:00:00+00:00")
        self.assertEqual(r["candles"][0]["timestamp"],"2026-08-07T10:00:00+05:30")


if __name__=="__main__":unittest.main()
