import unittest
from app.current_mind_integrated_replay import current_mind_click
class IntegratedTests(unittest.TestCase):
 def test_click_is_journaled_without_fabricated_trade(self):
  x=current_mind_click(click_timestamp="2026-08-25T14:00:00+05:30",context_records=[],market_features={},evidence_items=[])
  self.assertEqual(x["decision"]["action"],"NO_TRADE");self.assertTrue(x["decision_fingerprint"])
if __name__=="__main__":unittest.main()
