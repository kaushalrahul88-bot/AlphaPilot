import unittest
from app.point_in_time_derivatives_context import align_option_snapshot_with_candles

class PointInTimeDerivativesContextTests(unittest.TestCase):
    def test_partial_candle_after_snapshot_is_excluded(self):
        snap={"underlying_symbol":"NIFTY","expiry_date":"2026-09-03","observed_at":"2026-08-31T10:03:00+05:30","payload":{"x":1}}
        rows=[
            ["2026-08-31T09:55:00+05:30",1,2,1,2,10],
            ["2026-08-31T10:00:00+05:30",2,3,2,3,10],
        ]
        out=align_option_snapshot_with_candles(snap,rows,5)
        self.assertEqual(out["completed_candle_count"],1)
        self.assertEqual(out["latest_completed_candle_at"],"2026-08-31T10:00:00+05:30")
        self.assertTrue(out["point_in_time_safe"])

    def test_snapshot_at_bar_close_can_use_completed_bar(self):
        snap={"underlying_symbol":"NIFTY","observed_at":"2026-08-31T10:05:00+05:30","payload":{}}
        rows=[["2026-08-31T10:00:00+05:30",2,3,2,3,10]]
        out=align_option_snapshot_with_candles(snap,rows,5)
        self.assertEqual(out["completed_candle_count"],1)
        self.assertEqual(out["candle_age_minutes"],0.0)

    def test_underlying_join_key_is_normalized(self):
        snap={"underlying_symbol":" nifty ","observed_at":"2026-08-31T10:05:00+05:30","payload":{}}
        out=align_option_snapshot_with_candles(snap,[],5)
        self.assertEqual(out["join_key"]["underlying_symbol"],"NIFTY")

if __name__=="__main__":
    unittest.main()
