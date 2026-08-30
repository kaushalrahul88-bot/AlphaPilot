import unittest
from datetime import date
from app.current_mind_data_integrity_audit import audit_copper_replay_data,_expected_stamps

class DataIntegrityAuditTests(unittest.TestCase):
 def test_exact_session_is_clean(self):
  day=date(2026,8,24)
  xs=[[ts.isoformat(),100,101,99,100.5,10,20] for ts in _expected_stamps(day)]
  r=audit_copper_replay_data(xs,"COPPER31AUG26FUT","2026-08-31")
  self.assertEqual(r["dropped_by_ohlcv_cleaning"],0)
  self.assertEqual(r["duplicate_timestamp_count"],0)
  self.assertEqual(r["off_5m_grid_count"],0)
  self.assertEqual(r["outside_session_bars"],0)
if __name__=="__main__":unittest.main()
