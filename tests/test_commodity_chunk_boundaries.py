import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo
from app.commodity_backtest import _fetch_chunked

IST=ZoneInfo("Asia/Kolkata")

class ChunkBoundaryTests(unittest.IsolatedAsyncioTestCase):
 async def test_5m_chunks_keep_boundary_candle_at_next_chunk_start(self):
  start=datetime(2026,8,3,9,0,tzinfo=IST)
  end=datetime(2026,8,17,9,0,tzinfo=IST)
  calls=[]
  async def fake(provider,contract,interval,s,e):
   calls.append((s,e))
   return [[s.isoformat(),1,1,1,1,1,None],[e.isoformat(),1,1,1,1,1,None]]
  with patch("app.commodity_backtest._fetch_range",new=fake):
   await _fetch_chunked(object(),{"trading_symbol":"X"},5,start,end)
  self.assertEqual(calls[0][1],datetime(2026,8,10,8,55,tzinfo=IST))
  self.assertEqual(calls[1][0],datetime(2026,8,10,9,0,tzinfo=IST))
  self.assertEqual(calls[1][1],datetime(2026,8,17,8,55,tzinfo=IST))
  self.assertEqual(calls[2][0],datetime(2026,8,17,9,0,tzinfo=IST))

if __name__=="__main__":unittest.main()
