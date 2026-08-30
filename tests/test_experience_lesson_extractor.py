import unittest
from app.experience_lesson_extractor import *
class LessonTests(unittest.TestCase):
 def test_lucky_win_not_reinforced(self):
  r=extract_lessons({"decision":{"action":"BUY_CE"},"outcome":{"result":"TARGET"},"review":{"process_review":"INVALID"}})
  self.assertTrue(any("LUCKY_WIN" in x for x in r["lessons"]))
 def test_large_missed_move_requires_forensics(self):
  r=extract_lessons({"decision":{"action":"NO_TRADE"},"outcome":{"future_move_without_setup":True}})
  self.assertTrue(any("MISSED_MOVE_INVESTIGATION_REQUIRED" in x for x in r["lessons"]))
 def test_forensics_separates_perception_from_unforeseeable(self):
  r=investigate_missed_move({"decision_fingerprint":"x","decision":{"action":"NO_TRADE"}}, {"precursors":[
   {"factor":"inventory","knowable_before_move":True,"available_to_system":True,"captured_by_alphapilot":False},
   {"factor":"surprise headline","knowable_before_move":False}]})
  self.assertEqual(len(r["failure_modes"]["PERCEPTION_GAP"]),1)
  self.assertEqual(len(r["failure_modes"]["UNFORESEEABLE_OR_NEW_INFORMATION"]),1)
 def test_single_case_cannot_change_logic(self):
  a=aggregate_lessons([{"lessons":["EXIT_REVIEW: x"]}]);self.assertEqual(a["recurrent_patterns"],{})
if __name__=="__main__":unittest.main()
