import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import (
    BtcPitArchiveRecord,
    ImmutableBtcPitLedger,
    architecture_contract,
    archive_record_from_capture,
)


def _t(hour=4, minute=0, second=0):
    return datetime(2026, 9, 5, hour, minute, second, tzinfo=timezone.utc)


def _option_record(**overrides):
    values = dict(
        dataset="COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES",
        provider="COINDCX_CAPTURE",
        source_key="BTC-CALL-100000@2026-09-05T04:00:00Z",
        first_seen_at=_t(),
        event_at=_t(3, 59, 59),
        source_version="capture-v1",
        payload={
            "symbol": "BTC-CALL-100000",
            "bid": 99.0,
            "ask": 101.0,
            "delta": 0.5,
            "iv": 60.0,
            "open_interest": 100.0,
        },
    )
    values.update(overrides)
    return BtcPitArchiveRecord(**values)


class BtcPitArchiveTests(unittest.TestCase):
    def test_irrecoverable_option_snapshot_is_admitted(self):
        record = _option_record().validated()
        frozen = record.frozen_dict()
        self.assertTrue(frozen["point_in_time_proven"])
        self.assertEqual(len(frozen["payload_hash"]), 64)
        self.assertEqual(len(frozen["record_fingerprint"]), 64)

    def test_reconstructible_spot_candles_are_rejected(self):
        with self.assertRaises(ValueError):
            BtcPitArchiveRecord(
                dataset="BTC_SPOT_OHLCV",
                provider="COINDCX",
                source_key="spot-1",
                first_seen_at=_t(),
                payload={"close": 100_000.0},
            ).validated()

    def test_unproven_record_is_rejected(self):
        with self.assertRaises(ValueError):
            _option_record(point_in_time_proven=False).validated()

    def test_event_after_first_seen_is_rejected(self):
        with self.assertRaises(ValueError):
            _option_record(event_at=_t() + timedelta(seconds=1)).validated()

    def test_future_outcome_fields_are_rejected_nested(self):
        with self.assertRaises(ValueError):
            _option_record(payload={"symbol": "BTC", "diagnostic": {"future_return": 5.0}}).validated()

    def test_first_insert_wins_and_exact_duplicate_is_idempotent(self):
        ledger = ImmutableBtcPitLedger()
        first = ledger.insert_first_seen(_option_record())
        duplicate = ledger.insert_first_seen(_option_record())
        self.assertEqual(first["status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(duplicate["status"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(first["record"]["record_fingerprint"], duplicate["record"]["record_fingerprint"])

    def test_conflicting_later_payload_cannot_overwrite_first_seen(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(_option_record())
        with self.assertRaises(ValueError):
            ledger.insert_first_seen(_option_record(payload={
                "symbol": "BTC-CALL-100000",
                "bid": 120.0,
                "ask": 122.0,
                "delta": 0.6,
                "iv": 70.0,
                "open_interest": 150.0,
            }))
        visible = ledger.visible_as_of(_t() + timedelta(hours=1))
        self.assertEqual(visible[0]["payload"]["bid"], 99.0)

    def test_record_is_invisible_before_first_seen(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(_option_record())
        self.assertEqual(ledger.visible_as_of(_t() - timedelta(seconds=1)), [])
        self.assertEqual(len(ledger.visible_as_of(_t())), 1)

    def test_dataset_filter_is_point_in_time_safe(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(_option_record())
        funding = archive_record_from_capture(
            dataset="BTC_FUTURES_FUNDING_MARK_SNAPSHOT",
            provider="COINDCX",
            source_key="funding-1",
            first_seen_at=_t(4, 1),
            event_at=_t(4, 0, 59),
            payload={"funding_rate": 0.0001, "mark_price": 100_000.0},
        )
        ledger.insert_first_seen(funding)
        options = ledger.visible_as_of(_t(5), dataset="COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES")
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["dataset"], "COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES")

    def test_manifest_is_non_performance_and_immutable(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(_option_record())
        manifest = ledger.manifest()
        self.assertEqual(manifest["record_count"], 1)
        self.assertTrue(manifest["immutable_first_seen"])
        self.assertFalse(manifest["overwrite_allowed"])
        self.assertFalse(manifest["outcome_fields_allowed"])

    def test_architecture_keeps_storage_choice_open_but_semantics_frozen(self):
        contract = architecture_contract()
        self.assertFalse(contract["storage_backend_selected"])
        self.assertTrue(contract["semantic_contract_storage_agnostic"])
        self.assertFalse(contract["reconstructible_public_history_admitted"])
        self.assertTrue(contract["first_seen_wins"])
        self.assertFalse(contract["overwrite_existing_first_seen_record"])
        self.assertFalse(contract["future_outcome_fields_allowed"])
        self.assertFalse(contract["broker_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
