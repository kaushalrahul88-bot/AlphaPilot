import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.commodity_backtest import _fetch_chunked


IST = ZoneInfo("Asia/Kolkata")


class CommodityBacktestFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_fetch_discards_provider_rows_outside_requested_range(self):
        start = datetime(2026, 8, 24, 9, 0, tzinfo=IST)
        end = datetime(2026, 8, 25, 23, 30, tzinfo=IST)
        rows = [
            ["2026-08-20T09:00:00+05:30", 1, 2, 0.5, 1.5, 10],
            ["2026-08-24T09:00:00+05:30", 1, 2, 0.5, 1.5, 10],
            ["2026-08-25T18:35:00+05:30", 1, 2, 0.5, 1.5, 10],
            ["2026-08-26T09:00:00+05:30", 1, 2, 0.5, 1.5, 10],
        ]
        with patch("app.commodity_backtest._fetch_range", new=AsyncMock(return_value=rows)):
            result = await _fetch_chunked(object(), {}, 5, start, end)
        self.assertEqual([row[0] for row in result], [rows[1][0], rows[2][0]])


if __name__ == "__main__":
    unittest.main()
