import unittest
from app.experience_lesson_extractor import *
class LessonTests(unittest.TestCase):
 def test_lucky_win_not_reinforced(self):
  r=extract_lessons({"decision":{"action":"BUY_CE"},"outcome":{"result":"TARGET"},"review":{"process_review":"INVALID"}})
  self.assertTrue(any("LUCKY_WIN" in x for x in r["lessons"]))
 def test_single_case_cannot_change_logic(self):
  a=aggregate_lessons([{"lessons":["EXIT_REVIEW: x"]}]);self.assertEqual(a["recurrent_patterns"],{})
if __name__=="__main__":unittest.main()
