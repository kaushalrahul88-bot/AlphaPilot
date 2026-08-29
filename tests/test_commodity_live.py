import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx

from app.commodity_live import (
    _completed_rows,
    _expected_previous_weekday,
    _fetch_live_rows,
    _merge_rows,
    _previous_complete_session,
    _previous_session_state,
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

    def test_incomplete_expected_session_never_falls_back(self):
        monday = date(2026, 8, 24)
        tuesday = date(2026, 8, 25)
        source = rows(monday) + rows(tuesday, count=50)
        state = _previous_session_state(source, date(2026, 8, 26))
        self.assertIsNone(_previous_complete_session(source, date(2026, 8, 26)))
        self.assertEqual(state["expected_date"], tuesday)
        self.assertEqual(state["latest_observed_date"], tuesday)
        self.assertFalse(state["complete"])

    def test_missing_expected_session_never_uses_older_complete_day(self):
        friday = date(2026, 8, 21)
        state = _previous_session_state(rows(friday), date(2026, 8, 26))
        self.assertEqual(state["expected_date"], date(2026, 8, 25))
        self.assertEqual(state["latest_observed_date"], friday)
        self.assertFalse(state["checks"]["expected_session_present"])
        self.assertFalse(state["complete"])

    def test_monday_requires_friday_and_accepts_complete_session(self):
        monday = date(2026, 8, 31)
        friday = date(2026, 8, 28)
        self.assertEqual(_expected_previous_weekday(monday), friday)
        self.assertEqual(_previous_complete_session(rows(friday), monday), friday)

    def test_quote_payload_accepts_positive_live_price_only(self):
        self.assertEqual(_quote_payload({"payload": {"last_price": 27.5}})[1], 27.5)
        self.assertIsNone(_quote_payload({"payload": {"last_price": 0}}))

    def test_merge_rows_deduplicates_canonical_timestamp(self):
        day = date(2026, 8, 25)
        combined = rows(day, count=2)
        targeted = [combined[1], *rows(day, count=3)[2:]]
        merged = _merge_rows(combined, targeted)
        self.assertEqual(len(merged), 3)
        self.assertEqual(len({row[0].isoformat() for row in merged}), 3)

    async def test_exact_previous_day_is_fetched_for_all_timeframes(self):
        expected = date(2026, 8, 25)
        click = datetime(2026, 8, 26, 14, 0, tzinfo=IST)
        combined = rows(date(2026, 8, 26), count=10)
        targeted = rows(expected)
        fetch = AsyncMock(side_effect=[combined, targeted, combined, targeted[:58], combined, targeted[:15]])
        with patch("app.commodity_live._fetch_chunked", new=fetch):
            result, counts = await _fetch_live_rows(
                Provider(), {"trading_symbol": "TEST"}, datetime(2026, 8, 10, 9, 0, tzinfo=IST), click, expected,
            )
        self.assertEqual(counts, {"5m": 174, "15m": 58, "1h": 15})
        self.assertIn(expected, {row[0].date() for row in result["5m"]})
        targeted_calls = [fetch.await_args_list[index] for index in (1, 3, 5)]
        self.assertTrue(all(call.args[3].date() == expected and call.args[4].date() == expected for call in targeted_calls))

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
            patch("app.commodity_live._fetch_live_rows", new=AsyncMock(return_value=({key: history for key in ("5m", "15m", "1h")}, {"5m": 174, "15m": 58, "1h": 15}))),
            patch("app.commodity_live.fetch_mcx_option_master", new=AsyncMock()) as master,
        ):
            result = await run_commodity_live_scan(Provider(), datetime(2026, 8, 29, 12, 0, tzinfo=IST))
        self.assertEqual([row["decision_status"] for row in result["results"]], ["MARKET_CLOSED", "MARKET_CLOSED"])
        master.assert_not_awaited()
        self.assertFalse(result["live_execution_enabled"])

    async def test_directional_ready_never_emits_underlying_buy_sell_action(self):
        target=date(2026,8,26)
        click=datetime(2026,8,26,14,0,tzinfo=IST)
        current=rows(target,count=20)
        contract={"trading_symbol":"CRUDEOIL30SEP26FUT","tick_size":1}
        option={"trading_symbol":"CRUDEOIL17SEP267800CE","option_type":"CE","strike":7800,"buy_allowed":True}
        quality={"status":"VALID","checks":{}}
        previous_state={
            "expected_date":date(2026,8,25),
            "latest_observed_date":date(2026,8,25),
            "candles":174,
            "last_at":datetime(2026,8,25,23,25,tzinfo=IST),
            "checks":{},
            "complete":True,
        }
        previous={"underlying_direction":"BULLISH"}
        plan={"entry":100,"stop":98,"target1":103}
        directional={"status":"READY","action":"BUY","gates":{},"blockers":[]}
        strict={"status":"WAIT","action":"BUY","gates":{},"blockers":["premium unavailable"],"premium_setup":None}

        with (
            patch("app.commodity_live.resolve_nearest_mcx_future",new=AsyncMock(return_value=contract)),
            patch("app.commodity_live._fetch_live_rows",new=AsyncMock(return_value=({key:current for key in ("5m","15m","1h")},{"5m":174,"15m":58,"1h":15}))),
            patch("app.commodity_live._previous_session_state",return_value=previous_state),
            patch("app.commodity_live._data_quality",return_value=quality),
            patch("app.commodity_live.build_next_session_plan",return_value=previous),
            patch("app.commodity_live._live_mtf",return_value=({"5m":{},"15m":{},"1h":{}},plan,{"action":"BUY","alpha_score":75},{})),
            patch("app.commodity_live.fetch_benchmark_candles",new=AsyncMock(return_value={"candles":[]})),
            patch("app.commodity_live.benchmark_confirmation",return_value={}),
            patch("app.commodity_live.evaluate_commodity_click",side_effect=[directional,strict,directional,strict]),
            patch("app.commodity_live.fetch_mcx_option_master",new=AsyncMock(return_value=[option])),
            patch("app.commodity_live.select_mcx_option_contract",return_value=option),
            patch("app.commodity_live.fetch_live_mcx_option_quote",new=AsyncMock(return_value={"status":"UNAVAILABLE"})),
            patch("app.commodity_live.market_brain_audit",return_value={}),
            patch("app.commodity_live.mcx_session_status",return_value={"is_open":True,"status":"OPEN"}),
        ):
            result=await run_commodity_live_scan(Provider(),click)

        first=result["results"][0]
        self.assertEqual(first["decision_status"],"DIRECTIONAL_READY")
        self.assertEqual(first["action"],"NO TRADE")
        self.assertEqual(first["option_intent"],"BUY CE")
        self.assertEqual(first["underlying_action"],"BUY")
        self.assertEqual(first["directional_bias"],"BULLISH")
        self.assertFalse(first["underlying_setup"]["execution_eligible"])

    async def test_verified_exact_option_is_the_only_emitted_trade_action(self):
        target=date(2026,8,26)
        click=datetime(2026,8,26,14,0,tzinfo=IST)
        current=rows(target,count=20)
        contract={"trading_symbol":"CRUDEOIL30SEP26FUT","tick_size":1}
        option={"trading_symbol":"CRUDEOIL17SEP267800CE","option_type":"CE","strike":7800,"buy_allowed":True}
        quality={"status":"VALID","checks":{}}
        previous_state={
            "expected_date":date(2026,8,25),
            "latest_observed_date":date(2026,8,25),
            "candles":174,
            "last_at":datetime(2026,8,25,23,25,tzinfo=IST),
            "checks":{},
            "complete":True,
        }
        previous={"underlying_direction":"BULLISH"}
        plan={"entry":100,"stop":98,"target1":103}
        directional={"status":"READY","action":"BUY","gates":{},"blockers":[]}
        strict={"status":"READY","action":"BUY","gates":{},"blockers":[],"premium_setup":{"entry":42.0}}

        with (
            patch("app.commodity_live.resolve_nearest_mcx_future",new=AsyncMock(return_value=contract)),
            patch("app.commodity_live._fetch_live_rows",new=AsyncMock(return_value=({key:current for key in ("5m","15m","1h")},{"5m":174,"15m":58,"1h":15}))),
            patch("app.commodity_live._previous_session_state",return_value=previous_state),
            patch("app.commodity_live._data_quality",return_value=quality),
            patch("app.commodity_live.build_next_session_plan",return_value=previous),
            patch("app.commodity_live._live_mtf",return_value=({"5m":{},"15m":{},"1h":{}},plan,{"action":"BUY","alpha_score":75},{})),
            patch("app.commodity_live.fetch_benchmark_candles",new=AsyncMock(return_value={"candles":[]})),
            patch("app.commodity_live.benchmark_confirmation",return_value={}),
            patch("app.commodity_live.evaluate_commodity_click",side_effect=[directional,strict,directional,strict]),
            patch("app.commodity_live.fetch_mcx_option_master",new=AsyncMock(return_value=[option])),
            patch("app.commodity_live.select_mcx_option_contract",return_value=option),
            patch("app.commodity_live.fetch_live_mcx_option_quote",new=AsyncMock(return_value={"status":"AVAILABLE","premium":42.0,"contract":option})),
            patch("app.commodity_live.market_brain_audit",return_value={}),
            patch("app.commodity_live.mcx_session_status",return_value={"is_open":True,"status":"OPEN"}),
        ):
            result=await run_commodity_live_scan(Provider(),click)

        first=result["results"][0]
        self.assertEqual(first["decision_status"],"EXECUTABLE_READY")
        self.assertEqual(first["action"],"BUY CE")
        self.assertEqual(first["trade_instrument"],"OPTIONS")
        self.assertFalse(result["options_only_policy"]["futures_execution_allowed"])


if __name__ == "__main__":
    unittest.main()
