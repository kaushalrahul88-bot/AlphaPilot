import unittest
from app.frozen_market_candle_export import build_frozen_candle_artifact


class FrozenExportDictTests(unittest.TestCase):
    def test_dictionary_oi_zero_is_preserved(self):
        row={"timestamp":"2026-08-07T10:00:00+05:30","open":1,"high":2,"low":1,"close":2,"volume":3,"oi":0}
        r=build_frozen_candle_artifact([row],symbol="COPPER",trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                                       start=row["timestamp"],end=row["timestamp"],exported_at="2026-08-31T12:00:00+00:00")
        self.assertEqual(r["candles"][0]["open_interest"],0)


if __name__=="__main__":unittest.main()
