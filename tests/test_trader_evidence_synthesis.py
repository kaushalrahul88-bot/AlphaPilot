import unittest
from app.trader_evidence_synthesis import *
class EvidenceTests(unittest.TestCase):
 def test_correlated_items_are_one_lane(self):
  s=synthesize_evidence([{"lane":"STRUCTURE","stance":"BULLISH"},{"lane":"STRUCTURE","stance":"BULLISH"}])
  self.assertEqual(len(s["independent_bullish_lanes"]),1)
 def test_contradiction_preserved(self):
  s=synthesize_evidence([{"lane":"NEWS_REACTION","stance":"BULLISH"},{"lane":"NEWS_REACTION","stance":"BEARISH"}])
  self.assertIn("NEWS_REACTION",s["contradictory_lanes"])
if __name__=="__main__":unittest.main()
