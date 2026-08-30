import unittest
from datetime import datetime
from app.copper_news_persistence import assess_news_persistence
class NewsPersistenceTests(unittest.TestCase):
 def r(self,ts,h="Congo copper concentrate export ban",m="SUPPLY"):
  return {"available_at":ts,"value":{"headline":h},"news_intelligence":{"transmission_mechanism":m,"disposition":"ALLOW","effect":"BULLISH"}}
 def test_supply_news_can_persist_but_decays(self):
  x=assess_news_persistence(self.r("2026-08-08T09:45:00+05:30"),datetime.fromisoformat("2026-08-10T10:00:00+05:30"))
  self.assertEqual(x["status"],"ACTIVE_DECAYED");self.assertGreater(x["weight"],0)
 def test_old_news_expires(self):
  x=assess_news_persistence(self.r("2026-08-01T09:45:00+05:30"),datetime.fromisoformat("2026-08-10T10:00:00+05:30"))
  self.assertEqual(x["status"],"STALE_EXPIRED");self.assertEqual(x["weight"],0)
 def test_resolution_invalidates(self):
  old=self.r("2026-08-08T09:45:00+05:30");new=self.r("2026-08-10T09:00:00+05:30","Congo copper export ban lifted")
  x=assess_news_persistence(old,datetime.fromisoformat("2026-08-10T10:00:00+05:30"),[new])
  self.assertEqual(x["status"],"STALE_INVALIDATED")
if __name__=="__main__":unittest.main()
