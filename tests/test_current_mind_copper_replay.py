import unittest
from app.current_mind_copper_replay import _safe_memory_pool,_macro_evidence,_dominant_direction
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
