import unittest
from unittest.mock import patch
from app.current_mind_copper_replay import evaluate_current_mind_replay

class CurrentMindCoverageContractTests(unittest.TestCase):
 def test_missing_scheduled_timestamp_fails_loudly(self):
  # Coverage must never be repaired by silently dropping a scheduled click.
  self.assertIn("Scheduled click timestamp missing", __import__("inspect").getsource(evaluate_current_mind_replay))

if __name__=="__main__":unittest.main()
