import unittest
from app.current_mind_click_sampler import deterministic_clicks,sampler_contract
class SamplerTests(unittest.TestCase):
 def test_deterministic(self):
  xs=[{"timestamp":f"2026-08-24T{9+i//12:02d}:{(i%12)*5:02d}:00+05:30"} for i in range(80)]
  self.assertEqual(deterministic_clicks(xs,5),deterministic_clicks(xs,5))
 def test_normalized_ohlcv_rows_match_dict_timestamps(self):
  dict_rows=[{"timestamp":f"2026-08-24T{9+i//12:02d}:{(i%12)*5:02d}:00+05:30"} for i in range(80)]
  list_rows=[[x["timestamp"],100,101,99,100.5,1,None] for x in dict_rows]
  self.assertEqual(
   deterministic_clicks(dict_rows,5,seed="COPPER_CURRENT_MIND_V1_20_CLICKS"),
   deterministic_clicks(list_rows,5,seed="COPPER_CURRENT_MIND_V1_20_CLICKS"),
  )
 def test_min_global_index_preserves_twenty_clicks_without_unready_first_session_rows(self):
  rows=[["2026-08-03T%02d:%02d:00+05:30"%(9+i//12,(i%12)*5),100,101,99,100.5,1,None] for i in range(174)]
  clicks=deterministic_clicks(rows,20,seed="COPPER_CURRENT_MIND_V1_20_CLICKS",warmup_bars=24,tail_bars=12,min_global_index=50)
  self.assertEqual(len(clicks),20)
  eligible={rows[i][0] for i in range(50,162)}
  self.assertTrue(all(x["click_timestamp"] in eligible for x in clicks))
 def test_outcome_blind_contract(self):self.assertTrue(sampler_contract()["outcome_blind"])
if __name__=="__main__":unittest.main()
