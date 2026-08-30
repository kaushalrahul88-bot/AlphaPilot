import unittest
from app.copper_context_source_inventory import source_inventory
class InventoryTests(unittest.TestCase):
 def test_all_feeds_are_classified(self):
  r=source_inventory();self.assertEqual(len(r["feeds"]),9)
 def test_no_missing_option_history_is_claimed(self):
  self.assertEqual(source_inventory()["feeds"]["MCX_COPPER_OPTION"]["availability"],"FORWARD_COLLECTION")
if __name__=="__main__":unittest.main()
