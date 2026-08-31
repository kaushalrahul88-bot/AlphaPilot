import unittest
from app.storage_health import StorageHealthMonitor
class StorageHealthTests(unittest.TestCase):
 def test_threshold_order(self):
  x=StorageHealthMonitor("postgresql://example",warning_bytes=100,critical_bytes=200)
  self.assertEqual(x.warning_bytes,100);self.assertEqual(x.critical_bytes,200)
if __name__=="__main__":unittest.main()
