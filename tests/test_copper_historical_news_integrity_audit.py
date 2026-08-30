import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from datetime import datetime, timezone
from app.copper_historical_news import _fetch_day
from app.copper_historical_news_integrity_audit import audit_historical_news_records
class HistoricalNewsIntegrityTests(unittest.TestCase):
 def row(self,title,ts="2026-08-10T10:00:00+00:00"):
  return {"available_at":ts,"source":"example.com","value":{"headline":title,"language":"English","sentiment":"BULLISH","url":"https://example.com/x"}}
 def test_irrelevant_copper_theft_rejected(self):
  r=audit_historical_news_records([self.row("Police arrest suspects in copper theft")])
  self.assertEqual(r["classification_counts"].get("REJECT"),1)
 def test_market_copper_kept(self):
  r=audit_historical_news_records([self.row("Copper prices rise as LME inventories fall")])
  self.assertEqual(r["classification_counts"].get("KEEP"),1)
 def test_duplicate_cannot_vote_twice(self):
  rows=[self.row("Copper prices rise as LME inventories fall"),self.row("Copper prices rise as LME inventories fall","2026-08-10T10:05:00+00:00")]
  r=audit_historical_news_records(rows)
  self.assertEqual(r["classification_counts"].get("DUPLICATE"),1)
if __name__=="__main__":unittest.main()

class HistoricalNewsFetchResilienceTests(unittest.IsolatedAsyncioTestCase):
 async def test_retries_transient_timeout_without_silent_gap(self):
  client=MagicMock()
  response=MagicMock(); response.raise_for_status.return_value=None; response.json.return_value={"articles":[{"title":"Copper prices rise"}]}
  client.get=AsyncMock(side_effect=[httpx.ConnectTimeout("temporary"),response])
  with patch("app.copper_historical_news.asyncio.sleep",new=AsyncMock()):
   rows=await _fetch_day(client,datetime(2026,8,3,tzinfo=timezone.utc),datetime(2026,8,4,tzinfo=timezone.utc),attempts=2)
  self.assertEqual(len(rows),1)
  self.assertEqual(client.get.await_count,2)

 async def test_exhausted_slice_fails_closed(self):
  client=MagicMock(); client.get=AsyncMock(side_effect=httpx.ConnectTimeout("down"))
  with patch("app.copper_historical_news.asyncio.sleep",new=AsyncMock()):
   with self.assertRaises(RuntimeError):
    await _fetch_day(client,datetime(2026,8,3,tzinfo=timezone.utc),datetime(2026,8,4,tzinfo=timezone.utc),attempts=2)
