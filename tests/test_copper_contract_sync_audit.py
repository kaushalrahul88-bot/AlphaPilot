import unittest
from datetime import date

from app.copper_contract_sync_audit import _day_quality


class CopperContractSyncAuditTests(unittest.TestCase):
    def test_weekend_without_data_is_expected(self):
        r=_day_quality(date(2026,8,29),[],[])
        self.assertEqual(r["sync_state"],"CLOSED_AS_EXPECTED")
        self.assertEqual(r["expected_5m_bars"],0)

    def test_full_weekday_provider_store_match_is_synced(self):
        rows=[]
        for i in range(174):
            minutes=9*60+i*5
            hh,mm=divmod(minutes,60)
            rows.append([f"2026-08-28T{hh:02d}:{mm:02d}:00+05:30",1,1,1,1,1])
        r=_day_quality(date(2026,8,28),rows,rows)
        self.assertEqual(r["sync_state"],"SYNCED_COMPLETE")
        self.assertEqual(r["provider_expected_coverage_pct"],100.0)
        self.assertEqual(r["store_provider_timestamp_match_pct"],100.0)

    def test_weekday_provider_store_mismatch_is_not_silently_accepted(self):
        p=[["2026-08-28T09:00:00+05:30",1,1,1,1,1]]
        s=[["2026-08-28T09:05:00+05:30",1,1,1,1,1]]
        r=_day_quality(date(2026,8,28),p,s)
        self.assertEqual(r["sync_state"],"STORE_PROVIDER_MISMATCH")


if __name__=="__main__":
    unittest.main()
