from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.commodity_contract_continuity import (
    DEFAULT_ARCHIVE_LOOKBACK_DAYS,
    DEFAULT_GUARD_DAYS,
    assess_contract_continuity,
    retention_policy,
)
from app.commodity_direction_core import architecture_contract

IST = ZoneInfo("Asia/Kolkata")


def _contract(expiry: str = "2026-09-30") -> dict:
    return {
        "trading_symbol": "COPPER30SEP26FUT",
        "groww_symbol": "MCX-COPPER-30Sep26-FUT",
        "expiry_date": expiry,
    }


class CommodityContractContinuityTests(unittest.TestCase):
    def test_normal_contract_keeps_collecting_without_heavy_archive(self):
        result = assess_contract_continuity(
            _contract(), datetime(2026, 9, 5, 12, 0, tzinfo=IST)
        )
        self.assertEqual(result["stage"], "NORMAL")
        self.assertFalse(result["archive_required_now"])
        self.assertFalse(result["provider_history_after_expiry_assumed"])
        self.assertFalse(result["affects_direction"])

    def test_seven_day_window_requires_exact_contract_archive(self):
        result = assess_contract_continuity(
            _contract(), datetime(2026, 9, 23, 12, 0, tzinfo=IST)
        )
        self.assertEqual(result["days_to_expiry"], DEFAULT_GUARD_DAYS)
        self.assertEqual(result["stage"], "PRE_EXPIRY_ARCHIVE_WINDOW")
        self.assertTrue(result["archive_required_now"])
        self.assertIn("VERIFY_LOCAL_5M_ARCHIVE", result["required_actions"])
        self.assertFalse(result["silent_next_contract_substitution_allowed"])

    def test_expiry_session_requires_archive(self):
        result = assess_contract_continuity(
            _contract(), datetime(2026, 9, 30, 21, 0, tzinfo=IST)
        )
        self.assertEqual(result["stage"], "EXPIRY_SESSION")
        self.assertTrue(result["archive_required_now"])

    def test_expired_contract_never_assumes_provider_history_or_substitution(self):
        result = assess_contract_continuity(
            _contract("2026-08-31"), datetime(2026, 9, 5, 12, 0, tzinfo=IST)
        )
        self.assertEqual(result["stage"], "EXPIRED")
        self.assertFalse(result["provider_history_after_expiry_assumed"])
        self.assertFalse(result["silent_next_contract_substitution_allowed"])
        self.assertIn("DO_NOT_SUBSTITUTE_NEXT_CONTRACT", result["required_actions"])

    def test_incomplete_identity_fails_closed(self):
        result = assess_contract_continuity(
            {"trading_symbol": "COPPER30SEP26FUT", "expiry_date": "2026-09-30"},
            datetime(2026, 9, 30, 12, 0, tzinfo=IST),
        )
        self.assertFalse(result["exact_contract_identity_complete"])
        self.assertTrue(result["archive_required_now"])

    def test_provider_lesson_is_data_provenance_not_alpha(self):
        policy = retention_policy()
        observed = policy["empirical_provider_observation"]
        self.assertEqual(observed["while_active"]["stored_rows_for_2026_08_03_through_2026_08_28"], 3318)
        self.assertEqual(observed["after_expiry"]["exact_contract_historical_5m_rows_returned"], 0)
        self.assertEqual(observed["control_check"]["historical_5m_rows_returned"], 696)
        self.assertFalse(policy["affects_direction"])
        self.assertFalse(policy["counts_as_causal_confirmation"])
        self.assertEqual(policy["pre_expiry_archive_lookback_days"], DEFAULT_ARCHIVE_LOOKBACK_DAYS)

    def test_shared_direction_core_carries_continuity_without_vote(self):
        contract = architecture_contract()
        self.assertIn("data_continuity", contract)
        self.assertFalse(contract["data_continuity_counts_as_direction_evidence"])
        self.assertFalse(contract["data_continuity"]["provider_history_after_expiry_assumed"])


if __name__ == "__main__":
    unittest.main()
