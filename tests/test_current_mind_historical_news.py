import unittest
from datetime import datetime,timezone
from app.current_mind_copper_replay import _news_evidence
class HistoricalNewsEvidenceTests(unittest.TestCase):
 def test_future_news_is_invisible(self):
  click=datetime(2026,8,10,10,0,tzinfo=timezone.utc)
  rows=[{"available_at":"2026-08-10T09:00:00+00:00","value":{"sentiment":"BULLISH","headline":"Copper rises"}},{"available_at":"2026-08-10T11:00:00+00:00","value":{"sentiment":"BEARISH","headline":"Copper falls"}}]
  x=_news_evidence(click,rows)
  self.assertEqual(x["stance"],"BULLISH");self.assertEqual(x["detail"]["visible_8h"],1)
if __name__=="__main__":unittest.main()
