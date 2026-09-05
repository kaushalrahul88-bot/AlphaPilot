import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger, archive_record_from_capture, architecture_contract
from app.crypto_btc_pit_postgres import SCHEMA_SQL, architecture_contract as postgres_contract


def _t(minute=0):
    return datetime(2026, 9, 5, 6, minute, tzinfo=timezone.utc)


def _record(first_seen_at, *, close=110.0):
    return archive_record_from_capture(
        dataset="BTC_OPEN_INTEREST",
        provider="COINGLASS_V4",
        source_key="BTC:OI:4h:1788573600000",
        first_seen_at=first_seen_at,
        event_at=_t() - timedelta(hours=4),
        source_version="COINGLASS_V4_AGGREGATED_OI_OHLC_V1",
        payload={
            "symbol": "BTC",
            "interval": "4h",
            "open_interest_open_usd": 100.0,
            "open_interest_close_usd": close,
        },
    )


class CryptoBtcPitRepollIdempotencyTests(unittest.TestCase):
    def test_same_provider_observation_seen_later_is_idempotent_and_preserves_earliest_seen(self):
        ledger = ImmutableBtcPitLedger()
        first = ledger.insert_first_seen(_record(_t()))
        later = ledger.insert_first_seen(_record(_t(5)))
        self.assertEqual(first["status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(later["status"], "IDEMPOTENT_DUPLICATE")
        visible = ledger.visible_as_of(_t(10), dataset="BTC_OPEN_INTEREST")
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["first_seen_at"], _t().isoformat())

    def test_changed_provider_content_for_same_event_is_still_rejected(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(_record(_t()))
        with self.assertRaises(ValueError):
            ledger.insert_first_seen(_record(_t(5), close=120.0))

    def test_postgres_schema_has_single_primary_key_declaration(self):
        self.assertEqual(SCHEMA_SQL.upper().count("PRIMARY KEY"), 1)

    def test_contracts_declare_earliest_first_seen_preservation(self):
        self.assertTrue(architecture_contract()["same_provider_observation_seen_later_is_idempotent"])
        self.assertTrue(architecture_contract()["earliest_first_seen_is_preserved"])
        self.assertTrue(postgres_contract()["same_provider_observation_seen_later_is_idempotent"])
        self.assertTrue(postgres_contract()["earliest_first_seen_is_preserved"])


if __name__ == "__main__":
    unittest.main()
