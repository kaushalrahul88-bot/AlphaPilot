import unittest
from app.frozen_market_candle_export import build_frozen_candle_artifact


class FrozenExportMetadataTests(unittest.TestCase):
    def test_artifact_declares_point_in_time_and_no_refetch(self):
        r=build_frozen_candle_artifact([],symbol="COPPER",trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                                       start="2026-08-03T09:00:00+05:30",end="2026-08-28T23:30:00+05:30",
                                       exported_at="2026-08-31T12:00:00+00:00")
        self.assertTrue(r["point_in_time"]);self.assertFalse(r["network_refetch"])
        self.assertEqual(r["mode"],"FROZEN_MARKET_CANDLES_V1")


if __name__=="__main__":unittest.main()
