import unittest
from app.comex_hg_acquisition import *
class ComexTests(unittest.TestCase):
 def test_no_credentials_blocks_replay(self):self.assertFalse(acquisition_status(credentials_configured=False)["replay_allowed"])
 def test_entitlement_required(self):self.assertEqual(acquisition_status(credentials_configured=True,entitled_files=0)["state"],"ENTITLEMENT_REQUIRED")
 def test_normalizes_timestamped_bar(self):
  x=normalize_comex_bar({"timestamp":"2026-08-25T14:15:00+05:30","close":6.7},source="CME DataMine",available_at="2026-08-25T14:16:00+05:30")
  self.assertEqual(x["series"],"COMEX_HG")
if __name__=="__main__":unittest.main()
