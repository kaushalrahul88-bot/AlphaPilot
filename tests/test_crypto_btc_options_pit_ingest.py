import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_options_pit_ingest import (
    OPTION_EXIT_QUOTES_DATASET,
    OPTIONS_CHAIN_DATASET,
    VerifiedBtcOptionPitCapture,
    architecture_contract,
    ingest_verified_option_capture,
)
from app.crypto_btc_pit_archive import ImmutableBtcPitLedger


def _t(minute=0):
    return datetime(2026, 9, 5, 7, minute, tzinfo=timezone.utc)


def _capture(**overrides):
    values = dict(
        symbol="BTC-100000-CALL-TEST",
        option_type="CALL",
        strike=100000.0,
        expiry_at=_t() + timedelta(days=1),
        observed_at=_t() - timedelta(seconds=5),
        first_seen_at=_t(),
        bid=99.0,
        ask=101.0,
        mark=100.0,
        delta=0.50,
        gamma=0.00001,
        theta=-1.0,
        vega=2.0,
        implied_volatility=60.0,
        open_interest=100.0,
        volume_24h=50.0,
        source_type="VERIFIED_COINDCX_EXPORT",
        source_artifact_id="artifact-1",
        source_version="v1",
    )
    values.update(overrides)
    return VerifiedBtcOptionPitCapture(**values)


class CryptoBtcOptionsPitIngestTests(unittest.IsolatedAsyncioTestCase):
    def test_verified_capture_uses_first_seen_for_selector_visibility(self):
        capture = _capture()
        snapshot = capture.snapshot()
        self.assertEqual(snapshot.observed_at, _t())
        self.assertEqual(snapshot.platform, "COINDCX")
        self.assertEqual(snapshot.underlying, "BTC")
        self.assertEqual(snapshot.option_type, "CALL")

    def test_capture_builds_chain_and_exit_quote_archive_records(self):
        chain, quote = _capture().archive_records()
        self.assertEqual(chain.dataset, OPTIONS_CHAIN_DATASET)
        self.assertEqual(quote.dataset, OPTION_EXIT_QUOTES_DATASET)
        self.assertFalse(chain.payload["fabricated"])
        self.assertFalse(quote.payload["fabricated"])
        self.assertEqual(chain.first_seen_at, _t())

    def test_historical_rows_preserve_provider_event_and_first_seen(self):
        contract, quote = _capture().historical_rows()
        self.assertEqual(contract.event_at, _t() - timedelta(seconds=5))
        self.assertEqual(contract.available_at, _t())
        self.assertEqual(contract.visible_snapshot().observed_at, _t())
        self.assertEqual(quote.available_at, _t())
        self.assertTrue(contract.provenance.point_in_time_proven)
        self.assertTrue(contract.provenance.immutable_archive)

    async def test_ingestion_archives_both_chain_and_actual_quote_without_trade(self):
        ledger = ImmutableBtcPitLedger()
        result = await ingest_verified_option_capture(ledger, _capture())
        self.assertEqual(result["status"], "BTC_OPTIONS_PIT_CAPTURE_ARCHIVED")
        self.assertEqual(result["chain_storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(result["quote_storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(ledger.manifest()["record_count"], 2)
        self.assertFalse(result["fabricated"])
        self.assertFalse(result["options_trade_generated"])
        self.assertFalse(result["futures_trade_generated"])

    async def test_exact_capture_reingestion_is_idempotent(self):
        ledger = ImmutableBtcPitLedger()
        capture = _capture()
        await ingest_verified_option_capture(ledger, capture)
        second = await ingest_verified_option_capture(ledger, capture)
        self.assertEqual(second["chain_storage_status"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(second["quote_storage_status"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(ledger.manifest()["record_count"], 2)

    def test_capture_fails_closed_when_first_seen_precedes_provider_observation(self):
        with self.assertRaises(ValueError):
            _capture(observed_at=_t() + timedelta(seconds=1)).validated()

    def test_capture_fails_closed_on_bad_quote_or_unverified_source(self):
        with self.assertRaises(ValueError):
            _capture(bid=102.0, ask=101.0).validated()
        with self.assertRaises(ValueError):
            _capture(source_type="SCREENSHOT_FROM_UNKNOWN_SOURCE").validated()

    def test_architecture_contract_does_not_claim_or_reverse_engineer_options_api(self):
        contract = architecture_contract()
        self.assertFalse(contract["network_endpoint_discovered_by_this_module"])
        self.assertFalse(contract["private_endpoint_reverse_engineering_allowed"])
        self.assertFalse(contract["public_historical_options_api_claimed"])
        self.assertTrue(contract["verified_external_capture_required"])
        self.assertFalse(contract["option_chain_may_be_fabricated"])
        self.assertFalse(contract["futures_fallback_allowed"])


if __name__ == "__main__":
    unittest.main()
