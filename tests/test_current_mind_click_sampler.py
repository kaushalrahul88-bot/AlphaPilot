import unittest
from app.current_mind_click_sampler import deterministic_clicks,sampler_contract
class SamplerTests(unittest.TestCase):
 def test_deterministic(self):
  xs=[{"timestamp":f"2026-08-24T{9+i//12:02d}:{(i%12)*5:02d}:00+05:30"} for i in range(80)]
  self.assertEqual(deterministic_clicks(xs,5),deterministic_clicks(xs,5))
 def test_outcome_blind_contract(self):self.assertTrue(sampler_contract()["outcome_blind"])
if __name__=="__main__":unittest.main()
