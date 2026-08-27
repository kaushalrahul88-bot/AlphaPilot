import asyncio
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.commodity_benchmarks import benchmark_confirmation, fetch_benchmark_candles


IST = ZoneInfo("Asia/Kolkata")


def rows(click, bullish=True):
    output = []
    start = click - timedelta(minutes=30)
    price = 100.0
    for index in range(7):
        stamp = start + timedelta(minutes=index * 5)
        close = price + (0.10 if bullish else -0.10)
        output.append([stamp.isoformat(), price, max(price, close) + 0.02, min(price, close) - 0.02, close, 1000])
        price = close
    return output


class CommodityBenchmarkTests(unittest.TestCase):
    def test_bullish_wti_confirmation(self):
        click = datetime(2026, 8, 25, 18, 35, tzinfo=IST)
        result = benchmark_confirmation("CRUDEOIL", rows(click), click)
        self.assertEqual(result["symbol"], "WTI")
        self.assertEqual(result["direction"], "BULLISH")
        self.assertTrue(result["fresh"])

    def test_bearish_henry_hub_confirmation(self):
        click = datetime(2026, 8, 25, 18, 35, tzinfo=IST)
        result = benchmark_confirmation("NATURALGAS", rows(click, False), click)
        self.assertEqual(result["symbol"], "HENRY_HUB")
        self.assertEqual(result["direction"], "BEARISH")

    def test_copper_comex_confirmation(self):
        click = datetime(2026, 8, 25, 18, 35, tzinfo=IST)
        result = benchmark_confirmation("COPPER", rows(click), click)
        self.assertEqual(result["symbol"], "COMEX_COPPER")
        self.assertEqual(result["direction"], "BULLISH")

    def test_future_rows_are_excluded(self):
        click = datetime(2026, 8, 25, 18, 35, tzinfo=IST)
        base = benchmark_confirmation("CRUDEOIL", rows(click), click)
        contaminated = rows(click) + [[(click + timedelta(hours=1)).isoformat(), 1, 999, 1, 1, 999999]]
        self.assertEqual(base, benchmark_confirmation("CRUDEOIL", contaminated, click))

    def test_stale_benchmark_is_not_fresh(self):
        click = datetime(2026, 8, 25, 18, 35, tzinfo=IST)
        stale_rows = rows(click - timedelta(minutes=20))
        result = benchmark_confirmation("CRUDEOIL", stale_rows, click)
        self.assertFalse(result["fresh"])

    def test_fetch_parses_yahoo_chart_without_live_network(self):
        timestamps = [1787648700, 1787649000]
        payload = {"chart": {"error": None, "result": [{
            "timestamp": timestamps,
            "indicators": {"quote": [{
                "open": [70.0, 70.1], "high": [70.2, 70.3], "low": [69.9, 70.0],
                "close": [70.1, 70.2], "volume": [100, 200],
            }]},
        }]}}

        async def handler(request):
            self.assertIn("CL%3DF", str(request.url))
            return httpx.Response(200, json=payload)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await fetch_benchmark_candles(
                    "CRUDEOIL", "2026-08-25T09:00:00+05:30", "2026-08-25T10:00:00+05:30", client,
                )

        result = asyncio.run(run())
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["benchmark_symbol"], "WTI")
        self.assertEqual(len(result["candles"]), 2)
        self.assertFalse(result["execution_grade"])


if __name__ == "__main__":
    unittest.main()
