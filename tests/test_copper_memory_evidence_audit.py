import unittest
from app.copper_memory_evidence_audit import _choose

class MemoryEvidenceTests(unittest.TestCase):
    def mem(self,bp,sp,br=20,sr=20):
        return {"status":"READY","by_direction":{"BULLISH":{"resolved":br,"target_first_pct_resolved":bp},"BEARISH":{"resolved":sr,"target_first_pct_resolved":sp}}}
    def test_selects_buy_only_on_clear_evidence(self):self.assertEqual(_choose(self.mem(65,45)),"BUY")
    def test_selects_sell_only_on_clear_evidence(self):self.assertEqual(_choose(self.mem(40,60)),"SELL")
    def test_abstains_on_small_gap(self):self.assertEqual(_choose(self.mem(54,48)),"NO_TRADE")
    def test_abstains_on_small_sample(self):self.assertEqual(_choose(self.mem(70,30,10,20)),"NO_TRADE")
if __name__=="__main__":unittest.main()
