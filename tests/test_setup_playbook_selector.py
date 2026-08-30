import unittest
from app.setup_playbook_selector import *
class PlaybookTests(unittest.TestCase):
 def test_extension_blocks_trend_pullback_until_reset(self):
  r=eligible_playbooks(["TRENDING","EXTENDED"])
  self.assertNotIn("TREND_PULLBACK",[x["playbook"] for x in r["eligible"]])
 def test_incomplete_setup_waits(self):
  self.assertEqual(setup_candidate("FAILED_BREAKOUT",thesis="failure")["status"],"WAIT")
if __name__=="__main__":unittest.main()
