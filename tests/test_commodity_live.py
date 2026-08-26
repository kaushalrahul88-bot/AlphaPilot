import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx

from app.commodity_live import (
    _completed_rows,
    _previous_complete_session,
    _quote_payload,
    fetch_live_mcx_option_quote,
    run_commodity_live_scan,
)


IST = ZoneInfo("Asia/Kolkata")


def rows(day, count=174, minutes=5, close=100.0, volume=100.0):
    start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=IST)
    return [
        [(start + timedelta(minutes=minutes * index)).isoformat(), close, close + 1, close - 1, close, volume]
        for index in range(count)
    ]


class Provider:
    BASE_URL = "https://api.groww.in"

    async def _headers(self):
        return {"Authorization": "Bearer test"}


class CommodityLiveTests(unittest.IsolatedAsyncioTestCase):
    def test_only_completed_candles_are_used(self):
        day = date(2026, 8, 26)
        source = rows(day, count=4)
        click = datetime(2026, 8, 26, 9, 17, tzinfo=IST)
        result = _completed_rows(source, click, 5)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[-1][0].time().isoformat(timespec="minutes"), "09:10")

    def test_previous_session_skips_incomplete_day(self):
        monday = date(2026, 8, 24)
        tuesday = date(2026, 8, 25)
        source = rows(monday) + rows(tuesday, count=50)
        self.assertEqual(_previous_complete_session(source, date(2026, 8, 26)), monday)

    def test_quote_payload_accepts_positive_live_price_only(self):
        self.assertEqual(_quote_payload({"payload": {"last_price": 27.5}})[1], 27.5)
        self.assertIsNone(_quote_payload({"payload": {"last_price": 0}}))

    async def test_exact_mcx_option_quote_uses_contract_trading_symbol(self):
        contract = {"trading_symbol": "CRUDEOIL17SEP267800PE", "strike": 7800, "option_type": "PE"}

        async def handler(request):
            self.assertEqual(request.url.params["exchange"], "MCX")
            self.assertEqual(request.url.params["segment"], "COMMODITY")
            self.assertEqual(request.url.params["trading_symbol"], contract["trading_symbol"])
            return httpx.Response(200, json={"payload": {"last_price": 42.0}})

        with patch("app.commodity_live.httpx.AsyncClient", return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            result = await fetch_live_mcx_option_quote(Provider(), contract)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["premium"], 42.0)

    async def test_closed_market_never_requests_option_master(self):
        target = date(2026, 8, 29)
        history = []
        for offset in range(1, 8):
            history += rows(target - timedelta(days=offset))
        contract = {"trading_symbol": "TESTFUT", "tick_size": 1}
        with (
            patch("app.commodity_live.resolve_nearest_mcx_future", new=AsyncMock(side_effect=[contract, contract])),
            patch("app.commodity_live._fetch_chunked", new=AsyncMock(return_value=history)),
            patch("app.commodity_live.fetch_mcx_option_master", new=AsyncMock()) as master,
        ):
            result = await run_commodity_live_scan(Provider(), datetime(2026, 8, 29, 12, 0, tzinfo=IST))
        self.assertEqual([row["decision_status"] for row in result["results"]], ["MARKET_CLOSED", "MARKET_CLOSED"])
        master.assert_not_awaited()
        self.assertFalse(result["live_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
