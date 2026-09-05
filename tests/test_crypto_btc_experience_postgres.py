import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_experience_postgres import (
    INSERT_SQL,
    SCHEMA_SQL,
    VISIBLE_STRICTLY_BEFORE_SQL,
    PostgresBtcExperienceStore,
    architecture_contract,
    postgres_experience_params,
)
from app.crypto_btc_experience_store import resolved_experience_record_from_entry


def _t(minutes=0):
    return datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _record():
    entry = {
        "click_id": "experience-postgres-1",
        "instrument_type": "OPTIONS",
        "decision_at": _t().isoformat(),
        "final_decision": "BUY_CALL",
        "future_outcome_may_rewrite_decision": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "outcome_type": "TRADE_CLOSED",
        "trade_outcome": {
            "status": "SHADOW_TRADE_CLOSED",
            "exit_at": _t(30).isoformat(),
            "net_pnl_account": 42.0,
            "actual_quote_used_for_pnl": True,
            "model_reference_used_as_fill": False,
        },
        "performance_eligible": True,
    }
    return resolved_experience_record_from_entry(entry=entry, resolved_at=_t(30))


class CryptoBtcExperiencePostgresTests(unittest.TestCase):
    def test_store_requires_explicit_database_url(self):
        with self.assertRaises(ValueError):
            PostgresBtcExperienceStore("")

    def test_postgres_params_preserve_resolved_experience_identity(self):
        record = _record()
        params = postgres_experience_params(record)
        self.assertEqual(params["natural_key"], record.natural_key)
        self.assertEqual(params["payload_hash"], record.payload_hash)
        self.assertEqual(params["record_fingerprint"], record.record_fingerprint)
        self.assertEqual(params["instrument_type"], "OPTIONS")
        self.assertEqual(params["outcome_type"], "TRADE_CLOSED")
        self.assertEqual(params["resolved_at"], _t(30))
        self.assertIn('"outcome_type":"TRADE_CLOSED"', params["payload"])

    def test_schema_is_insert_only_and_explicitly_resolved(self):
        schema = SCHEMA_SQL.upper()
        insert = INSERT_SQL.upper()
        self.assertIn("PRIMARY KEY", schema)
        self.assertIn("RESOLVED_AT", schema)
        self.assertIn("CHECK (RESOLVED_AT > DECISION_AT)", schema)
        self.assertIn("INSTRUMENT_TYPE = 'OPTIONS'", schema)
        self.assertIn("ON CONFLICT", insert)
        self.assertIn("DO NOTHING", insert)
        self.assertNotIn("DO UPDATE", insert)

    def test_visibility_query_is_strictly_before_not_same_timestamp(self):
        normalized = " ".join(VISIBLE_STRICTLY_BEFORE_SQL.upper().split())
        self.assertIn("WHERE RESOLVED_AT < %S", normalized)
        self.assertNotIn("RESOLVED_AT <=", normalized)

    def test_architecture_contract_keeps_outcome_memory_separate_and_nonexecuting(self):
        contract = architecture_contract()
        self.assertFalse(contract["backend_automatically_selected"])
        self.assertFalse(contract["schema_initialization_starts_collection"])
        self.assertFalse(contract["schema_initialization_starts_execution"])
        self.assertTrue(contract["insert_only"])
        self.assertFalse(contract["update_existing_record_allowed"])
        self.assertEqual(contract["visibility_operator"], "resolved_at < current_decision_at")
        self.assertFalse(contract["same_timestamp_resolution_visible"])
        self.assertFalse(contract["market_data_pit_archive_used_for_outcomes"])
        self.assertFalse(contract["futures_state_allowed"])
        self.assertFalse(contract["options_execution_enabled"])
        self.assertFalse(contract["futures_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
