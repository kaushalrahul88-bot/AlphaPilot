import unittest
from app.current_mind_copper_replay import _safe_memory_pool,_macro_evidence,_dominant_direction,_news_evidence
class ReplayTests(unittest.TestCase):
 def test_unresolved_recent_memory_is_hidden(self):
  xs=[{"timestamp":"2026-08-25T13:55:00+05:30","minutes_to_event":30}]
  self.assertEqual(_safe_memory_pool(xs,__import__("datetime").datetime.fromisoformat("2026-08-25T14:00:00+05:30")),[])
 def test_macro_before_aug17_has_pmi(self):
  r=_macro_evidence(__import__("datetime").datetime.fromisoformat("2026-08-10T14:00:00+05:30"))
  self.assertEqual(r["lane"],"MACRO")
 def test_two_independent_lanes_can_define_direction(self):
  self.assertEqual(_dominant_direction([{"lane":"STRUCTURE","stance":"BULLISH"},{"lane":"EXPERIENCE","stance":"BULLISH"}]),"BULLISH")
if __name__=="__main__":unittest.main()


class NewsCarryForwardTests(unittest.TestCase):
 def allowed(self,ts,effect="BULLISH",raw="BEARISH"):
  return {"available_at":ts,"value":{"headline":"Congo bans copper concentrate exports","sentiment":raw},
          "news_intelligence":{"disposition":"ALLOW","effect":effect}}

 def test_weekend_news_reaches_next_session(self):
  from datetime import datetime
  r=self.allowed("2026-08-08T09:45:00+05:30")
  e=_news_evidence(
   datetime.fromisoformat("2026-08-10T10:00:00+05:30"),[r],
   session_start=datetime.fromisoformat("2026-08-10T09:00:00+05:30"),
   previous_market_bar=datetime.fromisoformat("2026-08-07T23:25:00+05:30"))
  self.assertEqual(e["stance"],"BULLISH")
  self.assertEqual(e["detail"]["carried_from_closed_market"],1)

 def test_closed_market_carry_expires_after_eight_trading_hours(self):
  from datetime import datetime
  r=self.allowed("2026-08-08T09:45:00+05:30")
  e=_news_evidence(
   datetime.fromisoformat("2026-08-10T17:05:00+05:30"),[r],
   session_start=datetime.fromisoformat("2026-08-10T09:00:00+05:30"),
   previous_market_bar=datetime.fromisoformat("2026-08-07T23:25:00+05:30"))
  self.assertEqual(e["stance"],"UNKNOWN")
  self.assertEqual(e["detail"]["visible"],0)

 def test_news_seen_during_previous_session_is_not_revived(self):
  from datetime import datetime
  r=self.allowed("2026-08-07T20:00:00+05:30")
  e=_news_evidence(
   datetime.fromisoformat("2026-08-10T10:00:00+05:30"),[r],
   session_start=datetime.fromisoformat("2026-08-10T09:00:00+05:30"),
   previous_market_bar=datetime.fromisoformat("2026-08-07T23:25:00+05:30"))
  self.assertEqual(e["detail"]["visible"],0)

 def test_market_brain_uses_news_intelligence_effect_not_raw_sentiment(self):
  from datetime import datetime
  r=self.allowed("2026-08-10T09:30:00+05:30",effect="BULLISH",raw="BEARISH")
  e=_news_evidence(datetime.fromisoformat("2026-08-10T10:00:00+05:30"),[r])
  self.assertEqual(e["stance"],"BULLISH")

 def test_explicit_resolution_vetoes_prior_carried_news(self):
  from datetime import datetime
  old=self.allowed("2026-08-08T09:45:00+05:30")
  new={"available_at":"2026-08-10T09:15:00+05:30","value":{"headline":"Congo copper export ban lifted"},
       "news_intelligence":{"disposition":"CONTEXT_ONLY","effect":"UNKNOWN","transmission_mechanism":"SUPPLY"}}
  e=_news_evidence(datetime.fromisoformat("2026-08-10T10:00:00+05:30"),[old,new],
   session_start=datetime.fromisoformat("2026-08-10T09:00:00+05:30"),
   previous_market_bar=datetime.fromisoformat("2026-08-07T23:25:00+05:30"))
  self.assertEqual(e["detail"]["visible"],0)
  self.assertEqual(e["detail"]["stale_vetoed"],1)
