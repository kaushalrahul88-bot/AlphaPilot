import unittest
from datetime import datetime, timezone

from app.crypto_btc_pit_archive import archive_record_from_capture
from app.crypto_btc_pit_postgres import (
    INSERT_SQL,
    SCHEMA_SQL,
    PostgresBtcPitArchiveStore,
    architecture_contract,
    postgres_record_params,
)


def _t():
    return datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)


def _record():
    return archive_record_from_capture(
        dataset="BTC_FUTURES_FUNDING_MARK_SNAPSHOT",
        provider="COINDCX",
        source_key="B-BTC_USDT:1788588000000",
        first_seen_at=_t(),
        event_at=_t(),
        source_version="TEST_V1",
        payload={"funding_rate": 0.0001, "mark_price": 100000.0},
    )


class CryptoBtcPitPostgresTests(unittest.TestCase):
    def test_store_requires_explicit_database_url(self):
        with self.assertRaises(ValueError):
            PostgresBtcPitArchiveStore("")

    def test_postgres_params_preserve_immutable_archive_identity(self):
        record = _record()
        params = postgres_record_params(record)
        self.assertEqual(params["natural_key"], record.natural_key)
        self.assertEqual(params["payload_hash"], record.payload_hash)
        self.assertEqual(params["record_fingerprint"], record.record_fingerprint)
        self.assertEqual(params["dataset"], "BTC_FUTURES_FUNDING_MARK_SNAPSHOT")
        self.assertIn('"funding_rate":0.0001', params["payload"])

    def test_schema_is_insert_only_first_seen_shape(self):
        normalized_schema = SCHEMA_SQL.upper()
        normalized_insert = INSERT_SQL.upper()
        self.assertIn("PRIMARY KEY", normalized_schema)
        self.assertIn("POINT_IN_TIME_PROVEN", normalized_schema)
        self.assertIn("ON CONFLICT", normalized_insert)
        self.assertIn("DO NOTHING", normalized_insert)
        self.assertNotIn("DO UPDATE", normalized_insert)

    def test_architecture_contract_does_not_auto_select_or_start_backend(self):
        contract = architecture_contract()
        self.assertFalse(contract["backend_is_automatically_selected"])
        self.assertFalse(contract["schema_initialization_starts_collection"])
        self.assertTrue(contract["insert_only"])
        self.assertFalse(contract["update_existing_record_allowed"])
        self.assertFalse(contract["options_execution_enabled"])
        self.assertFalse(contract["futures_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
