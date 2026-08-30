import unittest
from app.news import _commodity_sentiment,_event_tags,COMMODITY_QUERIES
class CopperNewsTests(unittest.TestCase):
 def test_copper_supported(self):self.assertIn("COPPER",COMMODITY_QUERIES)
 def test_supply_disruption_is_bullish_context(self):self.assertEqual(_commodity_sentiment("COPPER","Copper rises after mine disruption"),"BULLISH")
 def test_inventory_build_is_bearish_context(self):self.assertEqual(_commodity_sentiment("COPPER","Copper falls as inventories rise"),"BEARISH")
 def test_tags_capture_copper_channels(self):self.assertIn("CHINA DEMAND",_event_tags("COPPER","China copper demand outlook"))
if __name__=="__main__":unittest.main()
