from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.fno_underlying_prospective_resolver_v1 import (
    _exact_future_bars,
    outcome_due_at,
)
from app.fno_underlying_prospective_store_v1 import IMMUTABILITY_SQL, SCHEMA_SQL
from app.fno_underlying_prospective_v1 import (
    BATCH_SIZE,
    CAPTURE_GRACE_SECONDS,
    CLICKS_PER_DAY,
    PRIMARY_UNIVERSE,
    architecture_contract,
    deterministic_batch,
    deterministic_clicks,
    due_click_slot,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


class FnoUnderlyingProspectiveV1Tests(unittest.TestCase):
    def test_schedule_is_frozen_unique_and_fully_resolvable(self):
        trade_date = date(2026, 9, 7)
        first = deterministic_clicks(trade_date)
        second = deterministic_clicks(trade_date)
        self.assertEqual(first, second)
        self.assertEqual(len(first), CLICKS_PER_DAY)
        self.assertEqual(len(set(first)), CLICKS_PER_DAY)
        for slot in first:
            local = slot.astimezone(IST)
            self.assertEqual(local.minute % 5, 0)
            self.assertGreaterEqual((local.hour, local.minute), (9, 30))
            self.assertLessEqual((local.hour, local.minute), (14, 0))
            self.assertLessEqual(
                slot + timedelta(minutes=90),
                datetime(2026, 9, 7, 15, 30, tzinfo=IST).astimezone(UTC),
            )

    def test_due_slot_fails_closed_outside_capture_grace(self):
        slot = deterministic_clicks(date(2026, 9, 7))[0]
        self.assertEqual(due_click_slot(slot), slot)
        self.assertEqual(due_click_slot(slot + timedelta(seconds=CAPTURE_GRACE_SECONDS)), slot)
        self.assertIsNone(due_click_slot(slot + timedelta(seconds=CAPTURE_GRACE_SECONDS + 1)))

    def test_batch_is_deterministic_four_symbols_and_historical_matched(self):
        slot = deterministic_clicks(date(2026, 9, 7))[3]
        first = deterministic_batch(slot)
        self.assertEqual(first, deterministic_batch(slot))
        self.assertEqual(len(first), BATCH_SIZE)
        self.assertEqual(len(set(first)), BATCH_SIZE)
        self.assertEqual(len(PRIMARY_UNIVERSE), 44)
        self.assertNotIn("LTIM", PRIMARY_UNIVERSE)
        self.assertNotIn("TATAMOTORS", PRIMARY_UNIVERSE)

    def test_outcome_due_horizons_and_exact_5m_path(self):
        slot = datetime(2026, 9, 7, 10, 0, tzinfo=IST).astimezone(UTC)
        self.assertEqual(outcome_due_at(slot, "15m"), slot + timedelta(minutes=15))
        self.assertEqual(
            outcome_due_at(slot, "EOD"),
            datetime(2026, 9, 7, 15, 30, tzinfo=IST).astimezone(UTC),
        )
        rows = []
        current = slot
        for i in range(3):
            value = 100.0 + i
            rows.append([current.isoformat(), value, value + 0.2, value - 0.2, value, 1000])
            current += timedelta(minutes=5)
        exact = _exact_future_bars(rows, slot, slot + timedelta(minutes=15))
        self.assertIsNotNone(exact)
        self.assertEqual(len(exact), 3)
        self.assertIsNone(_exact_future_bars(rows[:-1], slot, slot + timedelta(minutes=15)))

    def test_architecture_excludes_options_futures_execution(self):
        contract = architecture_contract()
        self.assertTrue(contract["underlying_only"])
        self.assertFalse(contract["options_used_for_decision"])
        self.assertFalse(contract["futures_used_for_decision"])
        self.assertFalse(contract["live_execution"])
        self.assertFalse(contract["broker_orders"])
        self.assertEqual(contract["capital_committed"], 0)
        self.assertEqual(contract["batch_size"], 4)
        self.assertEqual(contract["precommitted_random_clicks_per_day"], 20)

    def test_store_is_database_immutable_and_research_only(self):
        self.assertIn("BEFORE UPDATE OR DELETE", IMMUTABILITY_SQL)
        self.assertIn("BEFORE TRUNCATE", IMMUTABILITY_SQL)
        self.assertIn("CHECK (live_execution_enabled = FALSE)", SCHEMA_SQL)
        self.assertIn("CHECK (broker_orders_created = FALSE)", SCHEMA_SQL)
        self.assertIn("CHECK (options_used = FALSE)", SCHEMA_SQL)
        self.assertIn("CHECK (futures_used = FALSE)", SCHEMA_SQL)
        self.assertIn("CHECK (capital_committed = 0)", SCHEMA_SQL)


if __name__ == "__main__":
    unittest.main()
