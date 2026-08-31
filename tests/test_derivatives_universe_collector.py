import unittest
from app.derivatives_universe_collector import _expiry
class DerivativesUniverseCollectorTests(unittest.TestCase):
 def test_expiry(self):self.assertEqual(str(_expiry("2026-09-24")),"2026-09-24")
if __name__=="__main__":unittest.main()
