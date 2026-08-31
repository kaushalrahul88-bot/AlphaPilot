import unittest

from app.market_news_volatility_context import assess_volatility_context


class MarketNewsVolatilityContextTests(unittest.TestCase):
    @staticmethod
    def _candle(day, clock, price):
        return {"timestamp":f"2026-08-{day:02d}T{clock}:00+05:30","close":price}

    def _in_session_candles(self, include_future=False):
        rows=[]
        for day, base in ((3,100.0),(4,101.0),(5,102.0),(6,103.0),(7,104.0)):
            rows.extend([
                self._candle(day,"10:00",base),
                self._candle(day,"10:05",base*1.001),
                self._candle(day,"10:30",base*1.002),
                self._candle(day,"11:00",base*1.003),
            ])
        if include_future:
            rows.extend([
                self._candle(8,"10:00",200.0),self._candle(8,"10:05",240.0),
                self._candle(8,"10:30",260.0),self._candle(8,"11:00",280.0),
            ])
        return rows

    def _in_session_window(self):
        return {
            "pre_event":{"timestamp":"2026-08-07T10:00:00+05:30","price":104.0},
            "immediate":{"timestamp":"2026-08-07T10:05:00+05:30","price":104.208},
            "confirmation":{"timestamp":"2026-08-07T10:30:00+05:30","price":104.312},
            "assimilation":{"timestamp":"2026-08-07T11:00:00+05:30","price":104.416},
        }

    def test_same_clock_prior_sessions_produce_shadow_baselines(self):
        result=assess_volatility_context(self._in_session_window(),self._in_session_candles())
        self.assertTrue(result["outcome_blind"])
        self.assertTrue(result["classification_unchanged"])
        immediate=result["segments"]["immediate"]
        self.assertEqual(immediate["status"],"AVAILABLE")
        self.assertEqual(immediate["samples"],4)
        self.assertAlmostEqual(immediate["median_abs_return"],0.001,places=8)
        self.assertAlmostEqual(immediate["actual_abs_return"],0.002,places=8)
        self.assertAlmostEqual(immediate["normalized_abs_move"],2.0,places=8)
        self.assertTrue(all(sample["end"] < "2026-08-07T10:00:00+05:30" for sample in immediate["sample_windows"]))

    def test_future_candles_cannot_change_point_in_time_baseline(self):
        base=assess_volatility_context(self._in_session_window(),self._in_session_candles())
        future=assess_volatility_context(self._in_session_window(),self._in_session_candles(include_future=True))
        self.assertEqual(base,future)

    def test_insufficient_prior_sessions_fails_closed(self):
        rows=[row for row in self._in_session_candles() if "2026-08-03" not in row["timestamp"] and "2026-08-04" not in row["timestamp"]]
        result=assess_volatility_context(self._in_session_window(),rows)
        self.assertEqual(result["segments"]["immediate"]["status"],"INSUFFICIENT_HISTORY")
        self.assertEqual(result["segments"]["immediate"]["samples"],2)

    def test_weekend_anchor_uses_prior_close_to_next_open_analogues(self):
        rows=[]
        session_days=(3,4,5,6,7,10)
        for index,day in enumerate(session_days):
            close=100.0+index
            rows.extend([
                self._candle(day,"09:05",close-0.4),
                self._candle(day,"09:30",close-0.2),
                self._candle(day,"10:00",close),
                self._candle(day,"23:25",close+0.5),
            ])
        window={
            "pre_event":{"timestamp":"2026-08-07T23:25:00+05:30","price":104.5},
            "immediate":{"timestamp":"2026-08-10T09:05:00+05:30","price":105.6},
            "confirmation":{"timestamp":"2026-08-10T09:30:00+05:30","price":105.8},
            "assimilation":{"timestamp":"2026-08-10T10:00:00+05:30","price":106.0},
        }
        result=assess_volatility_context(window,rows)
        immediate=result["segments"]["immediate"]
        self.assertEqual(immediate["status"],"AVAILABLE")
        self.assertGreaterEqual(immediate["samples"],3)
        self.assertTrue(all("T23:25:00" in sample["start"] for sample in immediate["sample_windows"]))
        self.assertTrue(all("T09:05:00" in sample["end"] for sample in immediate["sample_windows"]))
        self.assertTrue(all(sample["end"] < "2026-08-07T23:25:00+05:30" for sample in immediate["sample_windows"]))


if __name__=="__main__":unittest.main()
