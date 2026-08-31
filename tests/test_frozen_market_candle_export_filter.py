import unittest
from app.frozen_market_candle_export import build_frozen_candle_artifact


class FrozenExportFilterTests(unittest.TestCase):
    def test_outside_range_candles_do_not_affect_checksum(self):
        inside=["2026-08-07T10:00:00+05:30",2,2,2,2,2]
        outside=["2026-08-07T09:55:00+05:30",9,9,9,9,9]
        kwargs=dict(symbol="COPPER",trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                    start=inside[0],end=inside[0],exported_at="2026-08-31T12:00:00+00:00")
        a=build_frozen_candle_artifact([inside],**kwargs);b=build_frozen_candle_artifact([outside,inside],**kwargs)
        self.assertEqual(a["candles_sha256"],b["candles_sha256"])


if __name__=="__main__":unittest.main()
