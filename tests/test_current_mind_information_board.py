import unittest
from app.current_mind_information_board import information_board
class BoardTests(unittest.TestCase):
 def test_missing_context_is_explicit(self):
  r=information_board([],"2026-08-25T14:00:00+05:30")
  self.assertEqual(r["groups"]["global_copper"][0]["status"],"UNAVAILABLE")
 def test_absence_not_signal(self):
  self.assertIn("never converted",information_board([],"2026-08-25T14:00:00+05:30")["rule"])
if __name__=="__main__":unittest.main()
