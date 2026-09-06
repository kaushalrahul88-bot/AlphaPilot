from __future__ import annotations

import unittest
from datetime import date

from app.dashboard_market_universe_api import (
    GROWW_INSTRUMENT_CSV_URL,
    architecture_contract,
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
"""


class DashboardMarketUniverseTests(unittest.TestCase):
    def test_parses_only_active_buyable_nse_fno_underlyings(self):
        rows = parse_groww_fno_underlyings(CSV, as_of=date(2026, 9, 6))
        self.assertEqual([row["symbol"] for row in rows], ["NIFTY", "RELIANCE"])
        self.assertEqual(rows[0]["name"], "Nifty 50")
        self.assertEqual(rows[1]["name"], "Reliance Industries")
        self.assertTrue(all(row["state"] == "AVAILABLE" for row in rows))
        self.assertTrue(all(row["exchange"] == "NSE" and row["segment"] == "FNO" for row in rows))

    def test_rejects_unexpected_csv_schema(self):
        with self.assertRaises(ValueError):
            parse_groww_fno_underlyings("foo,bar\n1,2\n", as_of=date(2026, 9, 6))

    def test_architecture_is_public_read_only_and_no_trading(self):
        contract = architecture_contract()
        self.assertEqual(contract["fno_source"], "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER")
        self.assertFalse(contract["authentication_required"])
        self.assertFalse(contract["account_data_accessed"])
        self.assertTrue(contract["read_only"])
        self.assertFalse(contract["order_placement_enabled"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(GROWW_INSTRUMENT_CSV_URL, "https://growwapi-assets.groww.in/instruments/instrument.csv")


if __name__ == "__main__":
    unittest.main()
