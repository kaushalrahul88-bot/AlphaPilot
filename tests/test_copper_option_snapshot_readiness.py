import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.copper_option_snapshot_readiness import (
    run_snapshot_readiness,
    summarize_snapshot_readiness,
)


IST=ZoneInfo("Asia/Kolkata")


def row(day,buckets,snapshots,ce,pe,underlying,two_sided,contracts=20):
    return (
        date.fromisoformat(day),
        buckets,
        snapshots,
        ce,
        pe,
        underlying,
        two_sided,
        datetime.fromisoformat(day+"T09:05:00+05:30"),
        datetime.fromisoformat(day+"T23:25:00+05:30"),
        contracts,
    )


class CopperOptionSnapshotReadinessTests(unittest.IsolatedAsyncioTestCase):
    def test_readiness_is_descriptive_and_no_data_is_not_a_failure(self):
        result=summarize_snapshot_readiness([])
        self.assertEqual(result["status"],"NO_DATA")
        self.assertFalse(result["strategy_gate"])
        self.assertFalse(result["promotion_eligible"])
        self.assertEqual(result["snapshots"],0)

    def test_readiness_reports_bucket_side_and_quote_coverage(self):
        rows=[
            row("2026-08-31",170,3400,1700,1700,3400,2720),
            row("2026-09-01",160,3200,1600,1600,3040,1600),
        ]
        result=summarize_snapshot_readiness(rows)
        self.assertEqual(result["status"],"ACCUMULATING")
        self.assertEqual(result["trading_days"],2)
        self.assertEqual(result["snapshots"],6600)
        self.assertEqual(result["ce_snapshots"],3300)
        self.assertEqual(result["pe_snapshots"],3300)
        self.assertEqual(result["days_with_both_option_sides"],2)
        self.assertEqual(result["underlying_price_coverage_pct"],94.55)
        self.assertEqual(result["two_sided_quote_coverage_pct"],65.45)
        self.assertEqual(result["median_distinct_buckets_per_day"],165.0)
        self.assertTrue(result["daily"][0]["both_option_sides_present"])

    def test_twenty_days_only_unlocks_descriptive_replay_review_not_promotion(self):
        rows=[
            row(f"2026-09-{day:02d}",150,3000,1500,1500,3000,2400)
            for day in range(1,21)
        ]
        result=summarize_snapshot_readiness(rows)
        self.assertEqual(result["status"],"DESCRIPTIVE_REPLAY_SAMPLE_AVAILABLE")
        self.assertEqual(result["trading_days"],20)
        self.assertFalse(result["strategy_gate"])
        self.assertFalse(result["promotion_eligible"])

    async def test_readiness_initializes_empty_snapshot_store_before_query(self):
        fake_store=unittest.mock.MagicMock()
        fake_store.initialize=AsyncMock(return_value=None)
        with patch(
            "app.copper_option_snapshot_readiness.PostgresOptionSnapshotStore",
            return_value=fake_store,
        ) as store_cls, patch(
            "app.copper_option_snapshot_readiness._load_sync",
            return_value=summarize_snapshot_readiness([]),
        ):
            result=await run_snapshot_readiness("postgresql://example","COPPER",60)

        store_cls.assert_called_once_with("postgresql://example")
        fake_store.initialize.assert_awaited_once()
        self.assertEqual(result["status"],"NO_DATA")


if __name__=="__main__":
    unittest.main()
