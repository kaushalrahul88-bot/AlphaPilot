import unittest
from app.current_mind_thesis_builder import build_current_mind_decision
class BuilderTests(unittest.TestCase):
 def test_weak_evidence_no_trade(self):
  r=build_current_mind_decision({},{"regime_labels":[]},{"independent_bullish_lanes":[],"independent_bearish_lanes":[],"contradictory_lanes":[]},{},[])
  self.assertEqual(r["action"],"NO_TRADE")
 def test_good_direction_but_no_confirmation_waits(self):
  e={"independent_bullish_lanes":["STRUCTURE","PARTICIPATION"],"independent_bearish_lanes":[],"contradictory_lanes":[]}
  r=build_current_mind_decision({},{"regime_labels":["TRENDING"]},e,{},[])
  self.assertEqual(r["action"],"WAIT")
if __name__=="__main__":unittest.main()
