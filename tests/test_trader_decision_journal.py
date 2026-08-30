import unittest
from app.trader_decision_journal import *
class JournalTests(unittest.TestCase):
 def base(self):return journal_decision(click_timestamp="2026-08-25T14:00:00+05:30",information_board={},regime={},evidence={},scenario={},thesis={},decision={"action":"NO_TRADE"})
 def test_outcome_does_not_change_frozen_fingerprint(self):
  x=self.base();y=attach_outcome(x,{"future_move":"UP"});self.assertEqual(x["decision_fingerprint"],y["decision_fingerprint"])
 def test_original_decision_preserved(self):
  x=attach_outcome(self.base(),{"target":True});self.assertEqual(x["decision"]["action"],"NO_TRADE")
if __name__=="__main__":unittest.main()
