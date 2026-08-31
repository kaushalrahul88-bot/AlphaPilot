import unittest
from scripts.audit_market_news_reactions import audit


class ReactionAuditAsOfMetadataTests(unittest.TestCase):
    def test_report_records_cutoff(self):
        cutoff="2026-08-07T11:00:00+05:30"
        r=audit({"records":[]},[],as_of=cutoff)
        self.assertEqual(r["as_of"],cutoff)
        self.assertTrue(r["outcome_blind"]);self.assertFalse(r["outcomes_read"])


if __name__=="__main__":unittest.main()
