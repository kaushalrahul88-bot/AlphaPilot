import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.commodity_candle_collector import resolve_historical_mcx_contract
from app.commodity_continuous_backtest import _historical_future_contract


IST = ZoneInfo("Asia/Kolkata")


class HistoricalMcxContractDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_parses_canonical_groww_mcx_future_symbol(self):
        result = _historical_future_contract("COPPER", "MCX-COPPER-31Jul26-FUT")
        self.assertIsNotNone(result)
        self.assertEqual(result["expiry_date"], "2026-07-31")
        self.assertEqual(result["trading_symbol"], "COPPER31JUL26FUT")
        self.assertEqual(result["groww_symbol"], "MCX-COPPER-31Jul26-FUT")
        self.assertEqual(result["discovery_source"], "GROWW_HISTORICAL_CONTRACTS_API")

    def test_rejects_non_future_or_wrong_underlying(self):
        self.assertIsNone(_historical_future_contract("COPPER", "MCX-COPPER-31Jul26-850-CE"))
        self.assertIsNone(_historical_future_contract("COPPER", "MCX-GOLD-05Aug26-FUT"))

    async def test_resolver_chooses_nearest_archived_expiry(self):
        contracts = [
            {"expiry_date": "2026-06-30", "trading_symbol": "COPPER30JUN26FUT"},
            {"expiry_date": "2026-07-31", "trading_symbol": "COPPER31JUL26FUT"},
        ]
        discovery = {
            "supported": True,
            "source": "GROWW_HISTORICAL_CONTRACTS_API",
            "contracts": contracts,
            "diagnostics": [],
        }
        when = datetime(2026, 7, 10, 10, 0, tzinfo=IST)
        with patch(
            "app.commodity_continuous_backtest.discover_groww_historical_mcx_contracts",
            new=AsyncMock(return_value=discovery),
        ):
            result = await resolve_historical_mcx_contract(object(), "COPPER", when)
        self.assertEqual(result["trading_symbol"], "COPPER31JUL26FUT")

    async def test_resolver_fails_closed_when_archive_is_unavailable(self):
        discovery = {
            "supported": False,
            "source": "GROWW_HISTORICAL_CONTRACTS_API",
            "contracts": [],
            "diagnostics": [{"endpoint": "expiries", "status_code": 400}],
        }
        when = datetime(2026, 4, 10, 10, 0, tzinfo=IST)
        with patch(
            "app.commodity_continuous_backtest.discover_groww_historical_mcx_contracts",
            new=AsyncMock(return_value=discovery),
        ):
            with self.assertRaisesRegex(RuntimeError, "current instrument-master contracts are not accepted"):
                await resolve_historical_mcx_contract(object(), "COPPER", when)


if __name__ == "__main__":
    unittest.main()
