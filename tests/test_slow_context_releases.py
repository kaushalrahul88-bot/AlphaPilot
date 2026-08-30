import unittest
from app.slow_context_releases import fed_h10_available_at,china_nbs_available_at
class SlowContextTests(unittest.TestCase):
 def test_h10_release_converts_to_ist(self):
  self.assertIn("2026-08-25T01:45:00",fed_h10_available_at("2026-08-24"))
 def test_china_pmi_0930_is_0700_ist(self):
  self.assertIn("T07:00:00",china_nbs_available_at("2026-08-31","09:30"))
if __name__=="__main__":unittest.main()
