import json
import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_macro_live_availability_audit import MacroLiveAvailabilityAttempt
from app.crypto_macro_live_availability_postgres import (
    HAS_SUCCESS_SQL,
    INSERT_SQL,
    SCHEMA_SQL,
    PostgresMacroLiveAvailabilityStore,
    architecture_contract,
    attempt_natural_key,
    attempt_payload,
    payload_to_attempt,
    postgres_availability_params,
)


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
WINDOW_END = RELEASE + timedelta(minutes=10)


def _attempt(*, status="AVAILABLE_WITHIN_LATENCY", attempted_seconds=30, completed_seconds=31):
    success = status in {"AVAILABLE_WITHIN_LATENCY", "AVAILABLE_TOO_LATE"}
    return MacroLiveAvailabilityAttempt(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        release_at=RELEASE,
        reaction_window_end=WINDOW_END,
        attempted_at=WINDOW_END + timedelta(seconds=attempted_seconds),
        completed_at=WINDOW_END + timedelta(seconds=completed_seconds),
        status=status,
        availability_latency_seconds=float(completed_seconds),
        nasdaq_contract_ticker="NQU6" if success else None,
        euro_fx_contract_ticker="6EU6" if success else None,
        failure_kind=None if success else "ValueError",
    )


class CryptoMacroLiveAvailabilityPostgresTests(unittest.TestCase):
    def test_schema_is_insert_only_and_preserves_failed_attempts(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS", SCHEMA_SQL)
        self.assertIn("UNAVAILABLE_OR_PROVIDER_ERROR", SCHEMA_SQL)
        self.assertIn("attempt_fingerprint TEXT NOT NULL UNIQUE", SCHEMA_SQL)
        self.assertIn("ON CONFLICT (natural_key) DO NOTHING", INSERT_SQL)
        self.assertNotIn("UPDATE ", INSERT_SQL.upper())
        self.assertNotIn("DELETE ", INSERT_SQL.upper())
        self.assertIn("status = 'AVAILABLE_WITHIN_LATENCY'", HAS_SUCCESS_SQL)

    def test_postgres_params_preserve_exact_attempt_identity_and_fingerprint(self):
        attempt = _attempt()
        params = postgres_availability_params(attempt)
        self.assertEqual(params["event_key"], attempt.event_key)
        self.assertEqual(params["event_type"], attempt.event_type)
        self.assertEqual(params["status"], "AVAILABLE_WITHIN_LATENCY")
        self.assertEqual(params["attempt_fingerprint"], attempt.fingerprint())
        self.assertEqual(params["natural_key"], attempt_natural_key(attempt))
        self.assertEqual(json.loads(params["payload"]), attempt_payload(attempt))

    def test_failed_attempt_round_trip_remains_failure_not_neutral_market_data(self):
        attempt = _attempt(status="UNAVAILABLE_OR_PROVIDER_ERROR")
        restored = payload_to_attempt(attempt_payload(attempt))
        self.assertEqual(restored.status, "UNAVAILABLE_OR_PROVIDER_ERROR")
        self.assertEqual(restored.failure_kind, "ValueError")
        self.assertIsNone(restored.nasdaq_contract_ticker)
        self.assertIsNone(restored.euro_fx_contract_ticker)
        self.assertFalse(restored.direction_generated)

    def test_natural_key_changes_for_a_distinct_retrieval_attempt(self):
        first = _attempt(attempted_seconds=30, completed_seconds=31)
        second = _attempt(attempted_seconds=40, completed_seconds=41)
        self.assertNotEqual(attempt_natural_key(first), attempt_natural_key(second))
        self.assertNotEqual(first.fingerprint(), second.fingerprint())

    def test_store_requires_explicit_database_url(self):
        with self.assertRaisesRegex(ValueError, "database_url"):
            PostgresMacroLiveAvailabilityStore("")

    def test_architecture_keeps_operational_audit_separate_and_nonexecuting(self):
        contract = architecture_contract()
        self.assertTrue(contract["operational_audit_separate_from_market_data_pit"])
        self.assertTrue(contract["insert_only"])
        self.assertFalse(contract["update_existing_attempt_allowed"])
        self.assertFalse(contract["delete_existing_attempt_via_this_module_allowed"])
        self.assertTrue(contract["failed_attempts_preserved"])
        self.assertFalse(contract["schema_initialization_starts_collection"])
        self.assertFalse(contract["schema_initialization_enables_live_confirmation"])
        self.assertFalse(contract["schema_initialization_starts_execution"])
        self.assertFalse(contract["direction_generated"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
