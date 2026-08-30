import json,unittest
from pathlib import Path
from scripts.admit_copper_causal_events import classify
class AugustCausalLedgerTests(unittest.TestCase):
 def test_mixed_verified_events_remain_context(self):
  d=json.loads(Path("research/copper_causal_event_ledger_august_v1.json").read_text())
  for x in d["events"][:2]:self.assertEqual(classify(x)[0],"CONTEXT_ONLY")
 def test_date_only_candidate_cannot_be_allowed(self):
  d=json.loads(Path("research/copper_causal_event_ledger_august_v1.json").read_text())
  self.assertEqual(classify(d["events"][2])[0],"BLOCK")
if __name__=="__main__":unittest.main()
