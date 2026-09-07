import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_edge_attribution import run_crude_edge_attribution

IST = ZoneInfo("Asia/Kolkata")


def _candles(count=260):
    start = datetime(2026, 8, 17, 9, 0, tzinfo=IST)
    rows = []
    price = 6000.0
    for i in range(count):
        stamp = start + timedelta(minutes=5 * i)
        drift = 0.8 if (i // 35) % 2 == 0 else -0.7
        open_price = price
        close = max(100.0, price + drift + (0.15 if i % 5 else -0.1))
        rows.append([
            stamp.isoformat(),
            open_price,
            max(open_price, close) + 0.5,
            min(open_price, close) - 0.5,
            close,
            900 + (i % 20) * 40,
            50000 + i * 4,
        ])
        price = close
    return rows


class CrudeEdgeAttributionTests(unittest.TestCase):
    def test_attribution_is_development_only_no_news(self):
        result = run_crude_edge_attribution(
            _candles(),
            trading_symbol="CRUDEOIL21SEP26FUT",
            sample_every_bars=3,
            round_trip_cost_bps=4.0,
        )
        self.assertEqual(result["mode"], "ALPHAPILOT_CRUDE_EDGE_ATTRIBUTION_V1")
        self.assertFalse(result["news_enabled"])
        self.assertTrue(result["development_only"])
        self.assertFalse(result["current_reserved_partition_used_for_attribution"])
        self.assertFalse(result["brain_b_promoted"])
        self.assertIn("session", result["dimensions"])
        self.assertIn("price_oi_state", result["dimensions"])


if __name__ == "__main__":
    unittest.main()
