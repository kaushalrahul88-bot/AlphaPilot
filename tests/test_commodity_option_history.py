import asyncio
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from unittest.mock import AsyncMock, patch

from app.commodity_option_history import (
    _option_row,
    fetch_mcx_option_day,
    option_lot_affordability,
    premium_entry_after_click,
    probe_mcx_option_history,
    ranked_mcx_option_contracts,
    select_affordable_historical_mcx_option,
    scan_mcx_option_history_band,
    select_mcx_option_contract,
)


IST = ZoneInfo("Asia/Kolkata")


def contract(symbol, option_type, expiry, strike, groww_symbol=None):
    return {
        "underlying": symbol,
        "exchange": "MCX",
        "segment": "COMMODITY",
        "option_type": option_type,
        "expiry": expiry,
        "strike": float(strike),
        "groww_symbol": groww_symbol or f"MCX-{symbol}-{expiry}-{strike}-{option_type}",
        "trading_symbol": f"{symbol}{strike}{option_type}",
        "lot_size": 100 if symbol == "CRUDEOIL" else 2500 if symbol == "COPPER" else 1250,
        "tick_size": 0.1,
        "buy_allowed": True,
    }


class Provider:
    BASE_URL = "https://api.groww.in"

    async def _headers(self):
        return {"Authorization": "Bearer test"}


class CommodityOptionHistoryTests(unittest.TestCase):
    def test_normalizes_only_mcx_commodity_options(self):
        row = {
            "exchange": "MCX", "segment": "COMMODITY", "underlying_symbol": "CRUDEOIL",
            "instrument_type": "CE", "expiry_date": "2026-09-17", "strike_price": "8100",
            "groww_symbol": "MCX-CRUDEOIL-17Sep26-8100-CE", "trading_symbol": "CRUDEOIL17SEP268100CE",
            "lot_size": "100", "tick_size": "10.0", "buy_allowed": "1",
        }
        normalized = _option_row(row, {"CRUDEOIL"})
        self.assertEqual(normalized["tick_size"], 0.1)
        self.assertEqual(normalized["lot_size"], 100)
        self.assertTrue(normalized["buy_allowed"])
        self.assertIsNone(_option_row({**row, "exchange": "NSE"}, {"CRUDEOIL"}))

    def test_selects_nearest_crude_ce_strike_on_nearest_expiry(self):
        rows = [
            contract("CRUDEOIL", "CE", "2026-09-17", 8050),
            contract("CRUDEOIL", "CE", "2026-09-17", 8100),
            contract("CRUDEOIL", "CE", "2026-10-15", 8075),
        ]
        selected = select_mcx_option_contract(rows, "CRUDEOIL", date(2026, 8, 25), 8082, "CE")
        self.assertEqual(selected["expiry"], "2026-09-17")
        self.assertEqual(selected["expiry_dte"], 23)
        self.assertEqual(selected["strike"], 8100.0)

    def test_selects_nearest_natural_gas_pe(self):
        rows = [
            contract("NATURALGAS", "PE", "2026-09-23", 260),
            contract("NATURALGAS", "PE", "2026-09-23", 265),
        ]
        selected = select_mcx_option_contract(rows, "NATURALGAS", "2026-08-25", 263.2, "PE")
        self.assertEqual(selected["expiry_dte"], 29)
        self.assertEqual(selected["strike"], 265.0)

    def test_contract_more_than_35_dte_is_rejected(self):
        rows = [contract("CRUDEOIL", "CE", "2026-10-15", 8100)]
        self.assertIsNone(select_mcx_option_contract(rows, "CRUDEOIL", "2026-08-25", 8082, "CE"))

    def test_fetch_uses_mcx_commodity_and_parses_premium_candles(self):
        selected = contract("CRUDEOIL", "CE", "2026-09-17", 8100, "MCX-CRUDEOIL-17Sep26-8100-CE")
        payload = {"payload": {"candles": [
            ["2026-08-25T10:55:00+05:30", 100, 103, 99, 102, 25],
            ["2026-08-25T11:00:00+05:30", 102, 105, 101, 104, 30],
        ]}}

        async def handler(request):
            self.assertEqual(request.url.params["exchange"], "MCX")
            self.assertEqual(request.url.params["segment"], "COMMODITY")
            self.assertEqual(request.url.params["groww_symbol"], selected["groww_symbol"])
            return httpx.Response(200, json=payload)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await fetch_mcx_option_day(Provider(), selected, "2026-08-25", client)

        result = asyncio.run(run())
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["candles_available"], 2)

    def test_entry_is_next_option_candle_after_click(self):
        click = datetime(2026, 8, 25, 10, 55, tzinfo=IST)
        candles = [
            [click.isoformat(), 100, 101, 99, 100, 10],
            [(click + timedelta(minutes=5)).isoformat(), 102, 103, 101, 102, 10],
        ]
        entry = premium_entry_after_click(candles, click)
        self.assertEqual(entry["entry_price"], 102.0)
        self.assertEqual(entry["entry_at"], "2026-08-25T11:00:00+05:30")

    def test_research_probe_preserves_execution_boundaries(self):
        selected = contract("CRUDEOIL", "PE", "2026-09-17", 8100)
        history = {"status": "AVAILABLE", "contract": selected, "candles": [], "candles_available": 0}

        async def run():
            with patch("app.commodity_option_history.fetch_mcx_option_master", new=AsyncMock(return_value=[selected])), patch(
                "app.commodity_option_history.fetch_mcx_option_day", new=AsyncMock(return_value=history)
            ):
                return await probe_mcx_option_history(Provider(), "CRUDEOIL", "2026-08-25", 8082, "PE")

        result = asyncio.run(run())
        self.assertEqual(result["mode"], "MCX_OPTION_HISTORY_PROBE_V1")
        self.assertEqual(result["contract"]["strike"], 8100.0)
        self.assertFalse(result["production_rules_changed"])
        self.assertFalse(result["paper_trading_permission_changed"])
        self.assertFalse(result["live_execution_enabled"])

    def test_band_scan_fetches_ce_and_pe_once_per_selected_strike(self):
        master = []
        for option_type in ("CE", "PE"):
            for strike in (8000, 8050, 8100, 8150, 8200):
                master.append(contract("CRUDEOIL", option_type, "2026-09-17", strike))

        async def fake_history(provider, selected, trade_date, client=None):
            candles = [["2026-08-25T10:00:00+05:30", 10, 11, 9, 10, 1]] if selected["strike"] == 8100 else []
            return {"status": "AVAILABLE" if candles else "NO_CANDLES", "candles": candles}

        async def run():
            with patch("app.commodity_option_history.fetch_mcx_option_master", new=AsyncMock(return_value=master)), patch(
                "app.commodity_option_history.fetch_mcx_option_day", new=AsyncMock(side_effect=fake_history)
            ):
                return await scan_mcx_option_history_band(Provider(), "CRUDEOIL", "2026-08-25", 8100, radius=2)

        result = asyncio.run(run())
        self.assertEqual(result["contracts_tested"], 10)
        self.assertEqual(result["contracts_with_candles"], 2)
        self.assertEqual(result["total_candles"], 2)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertFalse(result["live_execution_enabled"])


    def test_copper_option_rows_are_supported(self):
        row = {
            "exchange": "MCX", "segment": "COMMODITY", "underlying_symbol": "COPPER",
            "instrument_type": "CE", "expiry_date": "2026-09-30", "strike_price": "950",
            "groww_symbol": "MCX-COPPER-30Sep26-950-CE", "trading_symbol": "COPPER30SEP26950CE",
            "lot_size": "2500", "tick_size": "5.0", "buy_allowed": "1",
        }
        normalized = _option_row(row, {"COPPER"})
        self.assertEqual(normalized["underlying"], "COPPER")
        self.assertEqual(normalized["lot_size"], 2500)

    def test_affordability_uses_whole_lots_under_15000(self):
        affordable = option_lot_affordability(2.0, 2500, 15000)
        self.assertTrue(affordable["affordable"])
        self.assertEqual(affordable["cost_per_lot_rupees"], 5000.0)
        self.assertEqual(affordable["lots"], 3)
        self.assertEqual(affordable["deployed_amount_rupees"], 15000.0)

        expensive = option_lot_affordability(7.0, 2500, 15000)
        self.assertFalse(expensive["affordable"])
        self.assertEqual(expensive["lots"], 0)

    def test_ranked_copper_contracts_start_nearest_to_atm(self):
        rows = [
            contract("COPPER", "CE", "2026-09-29", 900),
            contract("COPPER", "CE", "2026-09-29", 925),
            contract("COPPER", "CE", "2026-09-29", 950),
            contract("COPPER", "CE", "2026-10-30", 925),
        ]
        ranked = ranked_mcx_option_contracts(rows, "COPPER", "2026-08-25", 932, "CE")
        self.assertEqual([x["strike"] for x in ranked[:3]], [925.0, 950.0, 900.0])

    def test_affordable_selector_moves_outward_when_atm_is_too_expensive(self):
        rows = [
            contract("COPPER", "CE", "2026-09-29", 925),
            contract("COPPER", "CE", "2026-09-29", 950),
        ]
        click = datetime(2026, 8, 25, 10, 55, tzinfo=IST)

        async def fake_history(provider, selected, trade_date, client=None):
            premium = 8.0 if selected["strike"] == 925.0 else 4.0
            candles = [
                [click.isoformat(), premium, premium, premium, premium, 10],
                [(click + timedelta(minutes=5)).isoformat(), premium, premium, premium, premium, 10],
            ]
            return {"status": "AVAILABLE", "candles": candles}

        async def run():
            with patch(
                "app.commodity_option_history.fetch_mcx_option_day",
                new=AsyncMock(side_effect=fake_history),
            ):
                return await select_affordable_historical_mcx_option(
                    Provider(), rows, "COPPER", "2026-08-25", 932, "CE",
                    click, budget_rupees=15000,
                )

        result = asyncio.run(run())
        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["contract"]["strike"], 950.0)
        self.assertEqual(result["affordability"]["lots"], 1)
        self.assertEqual(result["affordability"]["deployed_amount_rupees"], 10000.0)
        self.assertEqual(result["attempts"][0]["status"], "TOO_EXPENSIVE")
        self.assertEqual(result["attempts"][1]["status"], "AFFORDABLE")


if __name__ == "__main__":
    unittest.main()
