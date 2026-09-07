import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.fno_underlying_prospective_resolver_v2 import (
    _exact_future_bars,
    architecture_contract as resolver_contract,
    outcome_due_at,
)
from app.fno_underlying_prospective_store_v2 import (
    SCHEMA_SQL,
    architecture_contract as store_contract,
)
from app.fno_underlying_prospective_v2 import (
    CAPTURE_GRACE_SECONDS,
    PRIMARY_UNIVERSE,
    PROTOCOL_ID,
    architecture_contract,
    deterministic_batch,
    deterministic_clicks,
    due_click_slot,
    move_potential_snapshot,
    schedule_manifest,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def _bar(stamp, close=100.0):
    return [stamp.isoformat(), close, close + 1, close - 1, close, 1000]


class FnoUnderlyingProspectiveV2Tests(unittest.TestCase):
    def test_frozen_schedule_has_20_unique_5m_slots(self):
        day = date(2026, 9, 7)
        first = deterministic_clicks(day)
        second = deterministic_clicks(day)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 20)
        self.assertEqual(len(set(first)), 20)
        for stamp in first:
            local = stamp.astimezone(IST)
            self.assertGreaterEqual((local.hour, local.minute), (9, 30))
            self.assertLessEqual((local.hour, local.minute), (14, 0))
            self.assertEqual(local.minute % 5, 0)
        manifest = schedule_manifest(day)
        self.assertFalse(manifest["backfill_allowed"])
        self.assertEqual(manifest["precommitted_count"], 20)

    def test_due_slot_is_strictly_grace_bounded(self):
        day = date(2026, 9, 7)
        slot = deterministic_clicks(day)[5]
        self.assertEqual(due_click_slot(slot), slot)
        self.assertEqual(
            due_click_slot(slot + timedelta(seconds=CAPTURE_GRACE_SECONDS)),
            slot,
        )
        self.assertIsNone(
            due_click_slot(slot + timedelta(seconds=CAPTURE_GRACE_SECONDS + 1))
        )

    def test_batch_is_deterministic_four_symbols_and_frozen_universe(self):
        slot = deterministic_clicks(date(2026, 9, 7))[0]
        a = deterministic_batch(slot)
        b = deterministic_batch(slot)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 4)
        self.assertEqual(len(set(a)), 4)
        self.assertEqual(len(PRIMARY_UNIVERSE), 44)
        self.assertNotIn("LTIM", PRIMARY_UNIVERSE)
        self.assertNotIn("TATAMOTORS", PRIMARY_UNIVERSE)

    def test_move_potential_is_descriptive_and_preoutcome(self):
        technical = {
            "timeframes": {
                "5m": {
                    "price": 100,
                    "atr14": 2,
                    "bollinger_upper": 105,
                    "bollinger_lower": 95,
                    "volume_ratio_raw": 1.5,
                    "rsi14": 55,
                    "market_structure": "UPTREND",
                    "signal": "WATCH_LONG",
                    "alpha_score": 63,
                },
                "15m": {"price": 200, "atr14": 1},
                "1h": {"price": 400, "atr14": 4},
            }
        }
        snapshot = move_potential_snapshot(technical)
        self.assertTrue(snapshot["diagnostic_only"])
        self.assertFalse(snapshot["influences_action"])
        self.assertTrue(snapshot["derived_before_outcome"])
        self.assertEqual(snapshot["timeframes"]["5m"]["atr_pct"], 2.0)
        self.assertEqual(snapshot["timeframes"]["5m"]["bollinger_width_pct"], 10.0)

    def test_exact_path_refuses_gaps(self):
        slot = datetime(2026, 9, 7, 10, 0, tzinfo=IST).astimezone(UTC)
        due = slot + timedelta(minutes=15)
        rows = [_bar(slot), _bar(slot + timedelta(minutes=5)), _bar(slot + timedelta(minutes=10))]
        self.assertIsNotNone(_exact_future_bars(rows, slot, due))
        self.assertIsNone(_exact_future_bars([rows[0], rows[2]], slot, due))
        self.assertEqual(outcome_due_at(slot, "15m"), due)

    def test_architecture_is_research_only_and_derivative_free(self):
        contract = architecture_contract()
        self.assertEqual(contract["version"], PROTOCOL_ID)
        self.assertTrue(contract["prospective"])
        self.assertTrue(contract["underlying_only"])
        self.assertFalse(contract["backfill_allowed"])
        self.assertTrue(contract["move_potential_recorded"])
        self.assertFalse(contract["move_potential_influences_action"])
        self.assertFalse(contract["options_used_for_decision"])
        self.assertFalse(contract["futures_used_for_decision"])
        self.assertFalse(contract["live_execution"])
        self.assertFalse(contract["broker_orders"])
        self.assertEqual(contract["capital_committed"], 0)

        resolver = resolver_contract()
        self.assertTrue(resolver["exact_5m_path_required"])
        self.assertFalse(resolver["partial_paths_persisted"])
        self.assertFalse(resolver["outcomes_can_change_decisions"])

        store = store_contract()
        self.assertTrue(store["database_immutable"])
        self.assertFalse(store["move_potential_can_influence_action"])
        self.assertFalse(store["live_execution"])

    def test_schema_enforces_no_execution_and_diagnostic_only_storage(self):
        lowered = SCHEMA_SQL.lower()
        self.assertIn("live_execution_enabled boolean not null check (live_execution_enabled = false)", lowered)
        self.assertIn("broker_orders_created boolean not null check (broker_orders_created = false)", lowered)
        self.assertIn("options_used boolean not null check (options_used = false)", lowered)
        self.assertIn("futures_used boolean not null check (futures_used = false)", lowered)
        self.assertIn("capital_committed numeric not null check (capital_committed = 0)", lowered)
        self.assertIn("move_potential_influenced_decision boolean not null check (move_potential_influenced_decision = false)", lowered)


if __name__ == "__main__":
    unittest.main()
