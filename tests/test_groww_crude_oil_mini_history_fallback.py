from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.providers.groww_rate_limited import RateLimitedGrowwProvider

IST=ZoneInfo("Asia/Kolkata")


class FakeResponse:
    def __init__(self,status_code,body):
        self.status_code=status_code
        self._body=body
    def json(self):
        return self._body
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    responses=[]
    calls=[]
    def __init__(self,*args,**kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self,*args):
        return False
    async def get(self,url,headers=None,params=None):
        self.__class__.calls.append((url,dict(params or {})))
        return self.__class__.responses.pop(0)


class MiniHistoryFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeClient.calls=[]
        FakeClient.responses=[]
        self.provider=object.__new__(RateLimitedGrowwProvider)
        self.provider._headers=AsyncMock(return_value={"Authorization":"Bearer test"})

    async def test_legacy_range_is_used_when_modern_route_is_empty(self):
        FakeClient.responses=[
            FakeResponse(200,{"status":"SUCCESS","payload":{"candles":[]}}),
            FakeResponse(200,{"status":"SUCCESS","payload":{"candles":[["2026-08-31T09:00:00",8200,8210,8190,8205,100]]}}),
        ]
        contract={
            "trading_symbol":"CRUDEOILM21SEP26FUT",
            "groww_symbol":"MCX-CRUDEOILM-21Sep26-FUT",
        }
        with patch.object(RateLimitedGrowwProvider,"_throttle",new=AsyncMock()), \
             patch("app.providers.groww_rate_limited.httpx.AsyncClient",FakeClient):
            rows=await self.provider._mini_fetch_chunk(
                contract,
                candle_interval="5minute",
                legacy_minutes=5,
                start=datetime(2026,8,31,9,0,tzinfo=IST),
                end=datetime(2026,8,31,23,30,tzinfo=IST),
            )
        self.assertEqual(len(rows),1)
        self.assertEqual(len(FakeClient.calls),2)
        self.assertTrue(FakeClient.calls[0][0].endswith("/v1/historical/candles"))
        self.assertTrue(FakeClient.calls[1][0].endswith("/v1/historical/candle/range"))
        self.assertEqual(FakeClient.calls[1][1]["trading_symbol"],"CRUDEOILM21SEP26FUT")
        self.assertEqual(FakeClient.calls[1][1]["interval_in_minutes"],"5")

    async def test_modern_history_short_circuits_legacy_route(self):
        FakeClient.responses=[
            FakeResponse(200,{"status":"SUCCESS","payload":{"candles":[["2026-09-01T09:00:00",8200,8210,8190,8205,100]]}}),
        ]
        contract={
            "trading_symbol":"CRUDEOILM21SEP26FUT",
            "groww_symbol":"MCX-CRUDEOILM-21Sep26-FUT",
        }
        with patch.object(RateLimitedGrowwProvider,"_throttle",new=AsyncMock()), \
             patch("app.providers.groww_rate_limited.httpx.AsyncClient",FakeClient):
            rows=await self.provider._mini_fetch_chunk(
                contract,
                candle_interval="5minute",
                legacy_minutes=5,
                start=datetime(2026,9,1,9,0,tzinfo=IST),
                end=datetime(2026,9,1,12,0,tzinfo=IST),
            )
        self.assertEqual(len(rows),1)
        self.assertEqual(len(FakeClient.calls),1)


if __name__ == "__main__":
    unittest.main()
