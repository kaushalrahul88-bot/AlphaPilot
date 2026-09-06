from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from app.crypto_btc_dashboard_api import _dashboard_status_from_rows, architecture_contract


UTC = timezone.utc


class CryptoBtcDashboardApiTests(unittest.TestCase):
    def test_empty_status_is_read_only_and_safe(self):
        result = _dashboard_status_from_rows(
            delta_summary=(0, None, None),
            delta_latest=None,
            thesis_summary=(0, 0, 0, 0, None),
            resolution_summary=(0, 0, 0, 0, 0, 0),
            shadow_summary=(0, 0, 0, 0),
            recent_shadow_rows=[],
        )
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["collection"]["snapshot_count"], 0)
        self.assertEqual(result["prospective_proof"]["resolved_count"], 0)
        self.assertIsNone(result["prospective_proof"]["directional_accuracy"])
        self.assertIsNone(result["live_shadow"]["latest"])
        self.assertFalse(result["safety"]["live_execution_enabled"])
        self.assertEqual(result["safety"]["capital_committed"], 0)

    def test_latest_shadow_click_exposes_only_safe_decision_and_outcome_summary(self):
        decision_at = datetime(2026, 9, 6, 3, 50, 10, tzinfo=UTC)
        due_at = datetime(2026, 9, 6, 7, 50, 10, tzinfo=UTC)
        resolution_at = datetime(2026, 9, 6, 7, 51, 0, tzinfo=UTC)
        click_payload = {
            "click_id": "crypto-live-shadow:test-1",
            "reason": "UNDERLYING_THESIS_UNKNOWN",
            "proof_bridge": {"decision_btc_price": 80010.32},
            "delta_reference_spot_price": 80010.30,
            "option_entry": None,
        }
        resolution_payload = {
            "outcome": {
                "status": "OUTCOME_RESOLVED",
                "classification": "ABSTENTION_RESOLVED",
                "entry_btc_price": 80010.32,
                "terminal_btc_price": 80410.0,
                "terminal_return_pct": 0.4995,
                "realized_direction": "UP",
                "directional_hit": None,
                "max_up_pct": 0.61,
                "max_down_pct": -0.12,
                "max_abs_move_pct": 0.61,
                "large_move_after_click": True,
                "large_move_missed_during_abstention": True,
                "performance_eligible": False,
            }
        }
        result = _dashboard_status_from_rows(
            delta_summary=(184, datetime(2026, 9, 5, 19, 37, tzinfo=UTC), datetime(2026, 9, 6, 5, 38, tzinfo=UTC)),
            delta_latest=(datetime(2026, 9, 6, 5, 38, tzinfo=UTC), date(2026, 9, 6), 79982.1, 14),
            thesis_summary=(1, 0, 1, 0, None),
            resolution_summary=(1, 0, 0, 0, 1, 1),
            shadow_summary=(1, 0, 1, 0),
            recent_shadow_rows=[(
                "test-1", decision_at, due_at, "UNKNOWN", "NO_TRADE_FROZEN",
                None, None, None, click_payload,
                "ABSTENTION_RESOLVED", resolution_at, resolution_payload,
            )],
        )
        latest = result["live_shadow"]["latest"]
        self.assertEqual(latest["market_direction"], "UNKNOWN")
        self.assertEqual(latest["reason"], "UNDERLYING_THESIS_UNKNOWN")
        self.assertEqual(latest["decision_btc_price"], 80010.32)
        self.assertIsNone(latest["option"])
        self.assertEqual(latest["resolution"]["classification"], "ABSTENTION_RESOLVED")
        self.assertTrue(latest["resolution"]["outcome"]["large_move_missed_during_abstention"])
        self.assertEqual(result["prospective_proof"]["abstention_large_move_missed_count"], 1)

    def test_option_shadow_entry_is_displayed_without_execution_capability(self):
        click_payload = {
            "click_id": "crypto-live-shadow:test-2",
            "reason": "FRESH_EXACT_DELTA_QUOTE",
            "proof_bridge": {"decision_btc_price": 80100.0},
            "delta_reference_spot_price": 80101.0,
            "option_entry": {
                "symbol": "C-BTC-80200-070926",
                "option_type": "CALL",
                "strike_price": 80200,
                "entry_ask": 210.0,
                "entry_bid": 205.0,
                "entry_mark": 207.0,
                "expiry_date": "2026-09-07",
                "snapshot_first_seen_at": "2026-09-06T05:00:00+00:00",
                "relative_spread_pct": 2.4,
                "open_interest": 42.0,
                "volume": 150.0,
                "greeks": {"delta": 0.49},
            },
        }
        result = _dashboard_status_from_rows(
            delta_summary=(1, datetime(2026, 9, 6, 5, 0, tzinfo=UTC), datetime(2026, 9, 6, 5, 0, tzinfo=UTC)),
            delta_latest=(datetime(2026, 9, 6, 5, 0, tzinfo=UTC), date(2026, 9, 7), 80101.0, 14),
            thesis_summary=(1, 1, 0, 1, datetime(2026, 9, 6, 9, 0, tzinfo=UTC)),
            resolution_summary=(0, 0, 0, 0, 0, 0),
            shadow_summary=(1, 1, 0, 0),
            recent_shadow_rows=[(
                "test-2", datetime(2026, 9, 6, 5, 0, 1, tzinfo=UTC), datetime(2026, 9, 6, 9, 0, 1, tzinfo=UTC),
                "BULLISH", "OPTIONS_SHADOW_ENTRY_FROZEN", "C-BTC-80200-070926", 210.0,
                datetime(2026, 9, 6, 5, 0, tzinfo=UTC), click_payload,
                None, None, None,
            )],
        )
        option = result["live_shadow"]["latest"]["option"]
        self.assertEqual(option["symbol"], "C-BTC-80200-070926")
        self.assertEqual(option["entry_ask"], 210.0)
        self.assertEqual(option["delta"], 0.49)
        self.assertFalse(result["safety"]["broker_order_placement_enabled"])

    def test_architecture_contract_is_read_only(self):
        contract = architecture_contract()
        self.assertTrue(contract["read_only"])
        self.assertFalse(contract["decision_creation_allowed"])
        self.assertFalse(contract["collection_start_allowed"])
        self.assertFalse(contract["options_trade_generation_allowed"])
        self.assertFalse(contract["futures_trade_generation_allowed"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
