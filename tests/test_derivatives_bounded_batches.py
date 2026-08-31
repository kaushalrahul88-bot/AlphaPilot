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
 async def option_chain(self,s,e): return {"symbol":s}

class BoundedCollectorTests(unittest.IsolatedAsyncioTestCase):
 @patch("app.derivatives_universe_collector.discover_active_derivatives_universe",new_callable=AsyncMock)
 async def test_bounded_batch_and_next_offset(self,discover):
  discover.return_value={"mcx_futures":[],"nse_futures":[],"fno_option_underlyings":["A","B","C","D","E"],
   "counts":{"mcx_futures":0,"nse_futures":0,"fno_option_underlyings":5}}
  d=await collect_derivatives_universe(Provider(),Store(),0,1,datetime(2026,8,31,10,tzinfo=ZoneInfo("Asia/Kolkata")),offset=0,limit=2)
  self.assertEqual(d["batch_size"],2);self.assertEqual(d["next_offset"],2);self.assertFalse(d["done"])
  d2=await collect_derivatives_universe(Provider(),Store(),0,1,datetime(2026,8,31,10,tzinfo=ZoneInfo("Asia/Kolkata")),offset=4,limit=2)
  self.assertEqual(d2["batch_size"],1);self.assertTrue(d2["done"])
if __name__=="__main__":unittest.main()
