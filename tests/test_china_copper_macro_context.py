import unittest
from app.china_copper_macro_context import china_copper_macro_records
from app.copper_point_in_time_context import latest_known_as_of
class ChinaMacroTests(unittest.TestCase):
 def test_aug17_release_hidden_before_ist_release(self):
  r=latest_known_as_of(china_copper_macro_records(),"2026-08-17T07:00:00+05:30")
  self.assertEqual(r["MACRO_RELEASE"]["value"]["event"],"CHINA_MANUFACTURING_PMI")
 def test_aug17_release_visible_after_release(self):
  r=latest_known_as_of(china_copper_macro_records(),"2026-08-17T08:00:00+05:30")
  self.assertIn(r["MACRO_RELEASE"]["value"]["event"],{"CHINA_INDUSTRIAL_VALUE_ADDED","CHINA_FIXED_ASSET_INVESTMENT","CHINA_RETAIL_SALES"})
if __name__=="__main__":unittest.main()
