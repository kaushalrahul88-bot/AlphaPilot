import unittest
from datetime import date,datetime
from unittest.mock import AsyncMock,patch
from zoneinfo import ZoneInfo

import httpx

from app.derivatives_universe_collector import (
 collect_derivatives_universe,
 _active_derivatives_from_rows,
 _expiry,
)

class DerivativesUniverseCollectorTests(unittest.TestCase):
 def test_expiry(self):self.assertEqual(str(_expiry("2026-09-24")),"2026-09-24")

 def test_discovers_mcx_option_underlyings_only_with_active_future_anchor(self):
  rows=[
   {"exchange":"MCX","segment":"COMMODITY","underlying_symbol":"GOLD","instrument_type":"FUT","expiry_date":"2026-09-30","trading_symbol":"GOLD30SEP26FUT","buy_allowed":"1"},
   {"exchange":"MCX","segment":"COMMODITY","underlying_symbol":"GOLD","instrument_type":"CE","expiry_date":"2026-09-30","trading_symbol":"GOLD30SEP26100000CE","buy_allowed":"1"},
   {"exchange":"MCX","segment":"COMMODITY","underlying_symbol":"SILVER","instrument_type":"PE","expiry_date":"2026-09-30","trading_symbol":"SILVER30SEP26100000PE","buy_allowed":"1"},
   {"exchange":"NSE","segment":"FNO","underlying_symbol":"RELIANCE","instrument_type":"PE","expiry_date":"2026-10-29","trading_symbol":"RELIANCE29OCT26PE","buy_allowed":"1"},
   {"exchange":"NSE","segment":"FNO","underlying_symbol":"RELIANCE","instrument_type":"CE","expiry_date":"2026-09-24","trading_symbol":"RELIANCE24SEP26CE","buy_allowed":"1"},
  ]
  result=_active_derivatives_from_rows(rows,date(2026,8,31))
  self.assertEqual(result["mcx_option_underlyings"],["GOLD"])
  self.assertEqual(result["fno_option_underlyings"],["RELIANCE"])
  self.assertEqual(result["fno_option_expiries"],{"RELIANCE":"2026-09-24"})
  self.assertEqual(result["counts"]["mcx_option_underlyings"],1)

class _Store:
 def __init__(self):self.saved=[]
 async def initialize(self):return None
 async def upsert_chain(self,underlying,expiry,observed,payload):
  self.saved.append((underlying,expiry,observed,payload))

class _Provider:
 def __init__(self,status=None):self.status=status
 async def expiries(self,symbol):return ["2026-09-29"]
 async def option_chain(self,symbol,expiry):
  if self.status is not None:
   request=httpx.Request("GET",f"https://api.groww.in/v1/option-chain/exchange/NSE/underlying/{symbol}?expiry_date={expiry}")
   response=httpx.Response(self.status,request=request)
   raise httpx.HTTPStatusError(f"HTTP {self.status}",request=request,response=response)
  return {"provider":"GROWW","symbol":symbol,"expiry":expiry,"data":{"payload":{"strikes":{}}}}

class DerivativesUniverseProviderActionabilityTests(unittest.IsolatedAsyncioTestCase):
 def _universe(self,symbol="NIFTYFPI"):
  return {"fno_option_underlyings":[symbol],"fno_option_expiries":{symbol:"2026-09-29"},
          "counts":{"mcx_futures":0,"nse_futures":0,"mcx_option_underlyings":0,"fno_option_underlyings":1}}

 async def _collect(self,provider,symbol="NIFTYFPI"):
  store=_Store()
  with patch("app.derivatives_universe_collector.discover_active_derivatives_universe",
             new=AsyncMock(return_value=self._universe(symbol))):
   result=await collect_derivatives_universe(
    provider,store,shard=0,shards=1,now=datetime(2026,8,31,16,0,tzinfo=ZoneInfo("Asia/Kolkata")),offset=0,limit=4,
   )
  return result,store

 async def test_option_chain_404_is_explicitly_unsupported_not_failed(self):
  result,store=await self._collect(_Provider(404))
  self.assertEqual(result["status"],"COLLECTED")
  self.assertEqual(result["option_chain_success"],0)
  self.assertEqual(result["option_chain_unsupported"],1)
  self.assertEqual(result["option_chain_failed"],0)
  self.assertEqual(result["unsupported_underlyings"],[{"underlying":"NIFTYFPI","http_status":404,"reason":"UNSUPPORTED_BY_PROVIDER"}])
  self.assertEqual(result["results"][0]["classification"],"UNSUPPORTED_BY_PROVIDER")
  self.assertFalse(result["results"][0]["retryable"])
  self.assertEqual(store.saved,[])

 async def test_option_chain_500_remains_collection_failure(self):
  result,store=await self._collect(_Provider(500))
  self.assertEqual(result["status"],"FAILED")
  self.assertEqual(result["option_chain_success"],0)
  self.assertEqual(result["option_chain_unsupported"],0)
  self.assertEqual(result["option_chain_failed"],1)
  self.assertEqual(result["unsupported_underlyings"],[])
  self.assertEqual(result["results"][0]["classification"],"COLLECTION_FAILURE")
  self.assertTrue(result["results"][0]["retryable"])
  self.assertEqual(store.saved,[])

 async def test_valid_option_chain_is_persisted(self):
  result,store=await self._collect(_Provider())
  self.assertEqual(result["status"],"COLLECTED")
  self.assertEqual(result["option_chain_success"],1)
  self.assertEqual(result["option_chain_unsupported"],0)
  self.assertEqual(result["option_chain_failed"],0)
  self.assertEqual(len(store.saved),1)
  self.assertEqual(store.saved[0][0],"NIFTYFPI")

if __name__=="__main__":unittest.main()