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

class PartialProvider(Provider):
 async def option_chain(self,s,e):
  if s=="B":raise RuntimeError("temporary provider failure")
  return await super().option_chain(s,e)

class MasterExpiryProvider(Provider):
 def __init__(self):self.requested=[]
 async def expiries(self,s):raise AssertionError("live master expiry should be authoritative")
 async def option_chain(self,s,e):
  self.requested.append((s,e));return {"symbol":s,"expiry":e}

class BoundedCollectorTests(unittest.IsolatedAsyncioTestCase):
 @patch("app.derivatives_universe_collector.discover_active_derivatives_universe",new_callable=AsyncMock)
 async def test_bounded_batch_and_next_offset(self,discover):
  discover.return_value={"mcx_futures":[],"nse_futures":[],"fno_option_underlyings":["A","B","C","D","E"],
   "counts":{"mcx_futures":0,"nse_futures":0,"fno_option_underlyings":5}}
  d=await collect_derivatives_universe(Provider(),Store(),0,1,datetime(2026,8,31,10,tzinfo=ZoneInfo("Asia/Kolkata")),offset=0,limit=2)
  self.assertEqual(d["batch_size"],2);self.assertEqual(d["next_offset"],2);self.assertFalse(d["done"])
  d2=await collect_derivatives_universe(Provider(),Store(),0,1,datetime(2026,8,31,10,tzinfo=ZoneInfo("Asia/Kolkata")),offset=4,limit=2)
  self.assertEqual(d2["batch_size"],1);self.assertTrue(d2["done"])

 @patch("app.derivatives_universe_collector.discover_active_derivatives_universe",new_callable=AsyncMock)
 async def test_partial_chain_batch_is_not_reported_as_collected(self,discover):
  discover.return_value={"mcx_futures":[],"nse_futures":[],"mcx_option_underlyings":[],
   "fno_option_underlyings":["A","B"],"counts":{"mcx_futures":0,"nse_futures":0,
   "mcx_option_underlyings":0,"fno_option_underlyings":2}}
  d=await collect_derivatives_universe(PartialProvider(),Store(),0,1,datetime(2026,8,31,10,tzinfo=ZoneInfo("Asia/Kolkata")),offset=0,limit=2)
  self.assertEqual(d["status"],"PARTIAL")
  self.assertEqual(d["option_chain_success"],1)
  self.assertEqual(d["option_chain_failed"],1)

 @patch("app.derivatives_universe_collector.discover_active_derivatives_universe",new_callable=AsyncMock)
 async def test_uses_nearest_live_master_expiry_before_historical_expiries_api(self,discover):
  discover.return_value={"mcx_futures":[],"nse_futures":[],"mcx_option_underlyings":[],
   "fno_option_underlyings":["NIFTYFPI"],"fno_option_expiries":{"NIFTYFPI":"2026-09-24"},
   "counts":{"mcx_futures":0,"nse_futures":0,"mcx_option_underlyings":0,"fno_option_underlyings":1}}
  provider=MasterExpiryProvider()
  d=await collect_derivatives_universe(provider,Store(),0,1,datetime(2026,8,31,10,tzinfo=ZoneInfo("Asia/Kolkata")),offset=0,limit=1)
  self.assertEqual(d["status"],"COLLECTED")
  self.assertEqual(d["results"],[{"symbol":"NIFTYFPI","expiry":"2026-09-24","expiry_source":"GROWW_INSTRUMENT_MASTER","ok":True}])
  self.assertEqual(provider.requested,[("NIFTYFPI","2026-09-24")])
if __name__=="__main__":unittest.main()
