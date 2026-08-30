import unittest
from app.copper_point_in_time_context import visible_at,latest_known_as_of,acquisition_manifest

class PITContextTests(unittest.TestCase):
 def test_future_publication_is_hidden(self):
  xs=[{"series":"COPPER_NEWS","observed_at":"2026-08-20T14:00:00+05:30","available_at":"2026-08-20T14:30:00+05:30","source":"x","value":"event"}]
  self.assertEqual(visible_at(xs,"2026-08-20T14:17:00+05:30"),[])
  self.assertEqual(len(visible_at(xs,"2026-08-20T14:31:00+05:30")),1)
 def test_latest_published_daily_report_carries_forward(self):
  xs=[
   {"series":"DAILY_REPORT","observed_at":"2026-08-24T20:00:00+05:30","available_at":"2026-08-24T20:00:00+05:30","source":"x","value":1},
   {"series":"DAILY_REPORT","observed_at":"2026-08-25T20:00:00+05:30","available_at":"2026-08-25T20:00:00+05:30","source":"x","value":2},
  ]
  r=latest_known_as_of(xs,"2026-08-25T14:00:00+05:30")
  self.assertEqual(r["DAILY_REPORT"]["value"],1)
  self.assertGreater(r["DAILY_REPORT"]["age_seconds"],0)
 def test_manifest_forbids_fabricated_option_history(self):
  m=acquisition_manifest()
  opt=next(x for x in m["feeds"] if x["series"]=="MCX_COPPER_OPTION")
  self.assertEqual(opt["status"],"COLLECT_FORWARD")
if __name__=="__main__":unittest.main()
