import unittest
from app.frozen_market_candle_export import build_frozen_candle_artifact


class FrozenMarketCandleExportTests(unittest.TestCase):
    def test_export_is_order_independent_and_checksum_stable(self):
        rows=[["2026-08-07T10:05:00+05:30",101,102,100,101.5,20,0],
              ["2026-08-07T10:00:00+05:30",100,101,99,100.5,10,1000]]
        kwargs=dict(symbol="COPPER",trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                    start="2026-08-07T10:00:00+05:30",end="2026-08-07T10:05:00+05:30",
                    exported_at="2026-08-31T12:00:00+00:00")
        a=build_frozen_candle_artifact(rows,**kwargs);b=build_frozen_candle_artifact(list(reversed(rows)),**kwargs)
        self.assertEqual(a["candles_sha256"],b["candles_sha256"])
        self.assertEqual(a["candles"],b["candles"])
        self.assertEqual(a["candles"][1]["open_interest"],0)

    def test_range_is_enforced(self):
        rows=[["2026-08-07T09:55:00+05:30",1,1,1,1,1],
              ["2026-08-07T10:00:00+05:30",2,2,2,2,2]]
        r=build_frozen_candle_artifact(rows,symbol="COPPER",trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                                       start="2026-08-07T10:00:00+05:30",end="2026-08-07T10:00:00+05:30",
                                       exported_at="2026-08-31T12:00:00+00:00")
        self.assertEqual(r["candle_count"],1)

    def test_duplicate_timestamps_fail_closed(self):
        row=["2026-08-07T10:00:00+05:30",2,2,2,2,2]
        with self.assertRaises(ValueError):
            build_frozen_candle_artifact([row,row],symbol="COPPER",trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                                         start=row[0],end=row[0],exported_at="2026-08-31T12:00:00+00:00")


if __name__=="__main__":unittest.main()
