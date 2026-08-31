import unittest
from app.frozen_market_candle_export import build_frozen_candle_artifact


class EmptyFrozenExportTests(unittest.TestCase):
    def test_empty_export_is_explicit_and_checksummed(self):
        r=build_frozen_candle_artifact([],symbol="COPPER",trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                                       start="2026-08-03T09:00:00+05:30",end="2026-08-28T23:30:00+05:30",
                                       exported_at="2026-08-31T12:00:00+00:00")
        self.assertEqual(r["candle_count"],0)
        self.assertIsNone(r["first_timestamp"]);self.assertIsNone(r["last_timestamp"])
        self.assertEqual(len(r["candles_sha256"]),64)


if __name__=="__main__":unittest.main()
