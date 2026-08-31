import unittest
from unittest.mock import AsyncMock,patch
from datetime import datetime
from zoneinfo import ZoneInfo
from app.derivatives_universe_collector import collect_derivatives_universe

class Store:
 async def initialize(self): pass
 async def upsert_chain(self,*args): pass

class Provider:
 async def expiries(self,s): return ["2026-09-24"]
 async def option_chain(self,s,e): return {"calls":[],"puts":[]}

class LiveStoragePolicyTests(unittest.IsolatedAsyncioTestCase):
 @patch("app.derivatives_universe_collector.discover_active_derivatives_universe",new_callable=AsyncMock)
 async def test_reconstructible_candles_are_not_persisted(self,discover):
  discover.return_value={"mcx_futures":[{"trading_symbol":"COPPER"}],"nse_futures":[{"trading_symbol":"NIFTY"}],
   "fno_option_underlyings":["NIFTY"],"counts":{"mcx_futures":1,"nse_futures":1,"fno_option_underlyings":1}}
  d=await collect_derivatives_universe(Provider(),Store(),0,1,datetime(2026,8,31,10,tzinfo=ZoneInfo("Asia/Kolkata")))
  self.assertFalse(d["historical_candles_persisted"])
  self.assertEqual(d["historical_candle_policy"],"FETCH_FROM_GROWW_ON_DEMAND")
  self.assertEqual(d["option_chain_success"],1)

if __name__=="__main__":unittest.main()
