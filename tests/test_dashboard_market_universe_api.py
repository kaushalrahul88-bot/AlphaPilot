from __future__ import annotations

import unittest
from datetime import date

from app.dashboard_market_universe_api import (
    DELTA_INDIA_PRODUCTS_URL,
    GROWW_INSTRUMENT_CSV_URL,
    architecture_contract,
    parse_delta_option_underlyings,
    parse_groww_commodity_underlyings,
    parse_groww_fno_underlyings,
)


CSV = """exchange,exchange_token,trading_symbol,groww_symbol,name,instrument_type,segment,series,isin,underlying_symbol,underlying_exchange_token,expiry_date,strike_price,lot_size,tick_size,freeze_quantity,is_reserved,buy_allowed,sell_allowed
NSE,1,NIFTY,NSE-NIFTY,Nifty 50,IDX,CASH,,,,,,,,,,0,1,1
NSE,2,RELIANCE,NSE-RELIANCE,Reliance Industries,EQ,CASH,EQ,INE002A01018,,,,1,,,,0,1,1
NSE,3,NIFTY26SEP25000CE,NSE-NIFTY-24Sep26-25000-CE,,CE,FNO,,,NIFTY,1,2026-09-24,25000,75,0.05,1000,0,1,1
NSE,4,RELIANCE26SEP3000CE,NSE-RELIANCE-24Sep26-3000-CE,,CE,FNO,,,RELIANCE,2,2026-09-24,3000,250,0.05,1000,0,1,1
NSE,5,OLD26AUG100CE,NSE-OLD-27Aug26-100-CE,,CE,FNO,,,OLD,5,2026-08-27,100,100,0.05,1000,0,1,1
NSE,6,LOCKED26SEP100CE,NSE-LOCKED-24Sep26-100-CE,,CE,FNO,,,LOCKED,6,2026-09-24,100,100,0.05,1000,0,0,1
BSE,7,SENSEX26SEP80000CE,BSE-SENSEX-24Sep26-80000-CE,,CE,FNO,,,SENSEX,7,2026-09-24,80000,20,0.05,1000,0,1,1
MCX,8,COPPER26SEPFUT,MCX-COPPER-30Sep26-FUT,,FUT,COMMODITY,,,COPPER,8,2026-09-30,,2500,0.05,1000,0,1,1
MCX,9,CRUDEOILM26SEPFUT,MCX-CRUDEOILM-18Sep26-FUT,,FUT,COMMODITY,,,CRUDEOILM,9,2026-09-18,,10,1,1000,0,1,1
MCX,10,GOLD26AUGFUT,MCX-GOLD-05Aug26-FUT,,FUT,COMMODITY,,,GOLD,10,2026-08-05,,1,1,1000,0,1,1
MCX,11,SILVER26SEPFUT,MCX-SILVER-04Sep26-FUT,,FUT,COMMODITY,,,SILVER,11,2026-09-30,,30,1,1000,0,0,1
"""

DELTA_PRODUCTS = [
    {"symbol": "C-BTC-80000-060926", "contract_type": "call_options", "state": "live", "underlying_asset": {"symbol": "BTC"}},
    {"symbol": "P-BTC-80000-060926", "contract_type": "put_options", "state": "live", "underlying_asset": {"symbol": "BTC"}},
    {"symbol": "C-ETH-4500-060926", "contract_type": "call_options", "state": "live", "underlying_asset": {"symbol": "ETH"}},
    {"symbol": "P-SOL-200-060926", "contract_type": "put_options", "state": "live"},
    {"symbol": "C-XAUT-4000-060926", "contract_type": "call_options", "state": "expired", "underlying_asset": {"symbol": "XAUT"}},
    {"symbol": "BTCUSD", "contract_type": "perpetual_futures", "state": "live", "underlying_asset": {"symbol": "BTC"}},
]


class DashboardMarketUniverseTests(unittest.TestCase):
    def test_parses_only_active_buyable_nse_fno_underlyings(self):
        rows = parse_groww_fno_underlyings(CSV, as_of=date(2026, 9, 6))
        self.assertEqual([row["symbol"] for row in rows], ["NIFTY", "RELIANCE"])
        self.assertEqual(rows[0]["name"], "Nifty 50")
        self.assertEqual(rows[1]["name"], "Reliance Industries")
        self.assertTrue(all(row["state"] == "AVAILABLE" for row in rows))
        self.assertTrue(all(row["exchange"] == "NSE" and row["segment"] == "FNO" for row in rows))

    def test_parses_active_buyable_mcx_commodity_underlyings_and_marks_connected_pipelines(self):
        rows = parse_groww_commodity_underlyings(CSV, as_of=date(2026, 9, 6))
        self.assertEqual([row["symbol"] for row in rows], ["COPPER", "CRUDEOILM"])
        self.assertTrue(all(row["state"] == "CONNECTED" for row in rows))
        self.assertTrue(all(row["exchange"] == "MCX" and row["segment"] == "COMMODITY" for row in rows))

    def test_parses_only_live_delta_option_underlyings(self):
        rows = parse_delta_option_underlyings(DELTA_PRODUCTS)
        self.assertEqual([row["symbol"] for row in rows], ["BTC", "ETH", "SOL"])
        self.assertEqual(rows[0]["state"], "CONNECTED")
        self.assertEqual(rows[1]["state"], "PLANNED")
        self.assertEqual(rows[2]["state"], "PLANNED")
        self.assertTrue(all(row["venue"] == "DELTA_EXCHANGE_INDIA" for row in rows))

    def test_rejects_unexpected_csv_schema(self):
        with self.assertRaises(ValueError):
            parse_groww_fno_underlyings("foo,bar\n1,2\n", as_of=date(2026, 9, 6))

    def test_architecture_is_public_read_only_and_no_trading(self):
        contract = architecture_contract()
        self.assertEqual(contract["fno_source"], "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER")
        self.assertEqual(contract["commodity_source"], "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER")
        self.assertEqual(contract["crypto_source"], "DELTA_INDIA_DOCUMENTED_PUBLIC_PRODUCTS")
        self.assertFalse(contract["authentication_required"])
        self.assertFalse(contract["account_data_accessed"])
        self.assertTrue(contract["read_only"])
        self.assertFalse(contract["order_placement_enabled"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(GROWW_INSTRUMENT_CSV_URL, "https://growwapi-assets.groww.in/instruments/instrument.csv")
        self.assertEqual(DELTA_INDIA_PRODUCTS_URL, "https://api.india.delta.exchange/v2/products")


if __name__ == "__main__":
    unittest.main()
