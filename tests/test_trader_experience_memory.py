import unittest
from app.trader_experience_memory import *
class MemoryTests(unittest.TestCase):
 def test_memory_keeps_process_and_outcome_separate(self):
  x=memory_case({"decision_fingerprint":"x","decision":{"action":"BUY_CE"},"outcome":{"result":"STOP"},"review":{"process_review":"VALID"}})
  self.assertEqual(x["action"],"BUY_CE");self.assertEqual(x["process_review"],"VALID")
 def test_similarity_not_outcome_based(self):
  c={"regime":{"regime_labels":["TRENDING"]},"evidence":{},"action":"NO_TRADE","outcome":{"result":"UP"}}
  r=retrieve_similar([c],{"regime":{"regime_labels":["TRENDING"]},"evidence":{}})
  self.assertEqual(len(r),1)
if __name__=="__main__":unittest.main()
