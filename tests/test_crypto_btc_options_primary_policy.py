import unittest
from datetime import datetime, timedelta, timezone

from app.coindcx_btc_public_provider import CoinDcxFuturesRtCapture
from app.crypto_btc_capture_scheduler import (
    COINDCX_FUTURES_RT_DATASET,
    architecture_contract as scheduler_contract,
    capture_gap_report,
    coindcx_futures_rt_archive_record,
)
from app.crypto_btc_historical_data_adapter import HistoricalProvenance
from app.crypto_btc_options_primary_policy import (
    COINDCX_OPTIONS_PIT_DATASET,
    CoinDcxOptionsCaptureReadiness,
    FUTURES_CONTEXT_ROLE,
    PRIMARY_TRADE_INSTRUMENT,
    architecture_contract,
    options_economic_data_gate,
    tag_futures_context_payload,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)


def _futures_capture():
    return CoinDcxFuturesRtCapture(
        first_seen_at=NOW,
        provider_snapshot_at=NOW - timedelta(milliseconds=100),
        provider_tick_at=NOW - timedelta(milliseconds=200),
        mark_price_at=NOW - timedelta(milliseconds=100),
        funding_rate=0.0001,
        estimated_funding_rate=0.00011,
        mark_price=80_000.0,
        last_price=80_001.0,
        price_change_pct_24h=0.5,
        volume_24h=1_000_000.0,
        market="BTCUSDT",
        raw_pair="B-BTC_USDT",
        provenance=HistoricalProvenance(
            provider="COINDCX",
            source_id="options-primary-boundary-test",
            availability_basis="FIRST_SEEN_CAPTURE",
            point_in_time_proven=True,
            immutable_archive=True,
            reconstructible_public_data=False,
        ),
    ).validated()


class CryptoBtcOptionsPrimaryPolicyTests(unittest.TestCase):
    def test_futures_payload_is_explicitly_options_context_only(self):
        tagged = tag_futures_context_payload({"mark_price": 80_000.0})
        self.assertEqual(tagged["primary_trade_instrument"], "OPTIONS")
        self.assertEqual(tagged["instrument_role"], FUTURES_CONTEXT_ROLE)
        self.assertTrue(tagged["context_only"])
        self.assertFalse(tagged["may_satisfy_options_contract_quote"])
        self.assertFalse(tagged["may_select_options_contract"])
        self.assertFalse(tagged["may_measure_options_entry_or_exit"])
        self.assertFalse(tagged["may_generate_futures_trade"])
        self.assertFalse(tagged["futures_quote_substitution_allowed"])

    def test_live_scheduler_record_carries_context_only_boundary(self):
        record = coindcx_futures_rt_archive_record(_futures_capture())
        self.assertEqual(record.dataset, COINDCX_FUTURES_RT_DATASET)
        self.assertEqual(record.payload["primary_trade_instrument"], PRIMARY_TRADE_INSTRUMENT)
        self.assertEqual(record.payload["instrument_role"], FUTURES_CONTEXT_ROLE)
        self.assertFalse(record.payload["may_satisfy_options_contract_quote"])
        self.assertFalse(record.payload["may_generate_options_trade_by_itself"])
        self.assertFalse(record.payload["may_generate_futures_trade"])
        self.assertEqual(record.source_version, "COINDCX_FUTURES_RT_OPTIONS_CONTEXT_V2")

    def test_options_economic_gate_rejects_futures_only_rows(self):
        result = options_economic_data_gate([
            {"dataset": COINDCX_FUTURES_RT_DATASET, "payload": {"mark_price": 80_000.0}},
        ])
        self.assertEqual(result["status"], "OPTIONS_ECONOMIC_DATA_UNAVAILABLE")
        self.assertEqual(result["qualifying_options_records"], 0)
        self.assertFalse(result["futures_substitution_used"])
        self.assertFalse(result["trade_generated"])

    def test_options_economic_gate_accepts_only_genuine_options_dataset_as_data_present(self):
        result = options_economic_data_gate([
            {"dataset": COINDCX_OPTIONS_PIT_DATASET, "payload": {"contract_symbol": "BTC-6SEP26-80000-C"}},
        ])
        self.assertEqual(result["status"], "OPTIONS_ECONOMIC_DATA_PRESENT")
        self.assertEqual(result["qualifying_options_records"], 1)
        self.assertFalse(result["trade_generated"])

    def test_coindcx_options_capture_is_blocked_without_verified_feed(self):
        status = CoinDcxOptionsCaptureReadiness().status()
        self.assertEqual(
            status["options_pit_capture_status"],
            "BLOCKED_NO_VERIFIED_DOCUMENTED_OR_AUTHORIZED_OPTIONS_FEED",
        )
        self.assertFalse(status["documented_or_authorized_options_feed"])
        self.assertFalse(status["undocumented_endpoint_reverse_engineering_allowed"])
        self.assertFalse(status["futures_may_satisfy_options_contract_quote"])
        self.assertFalse(status["live_execution_enabled"])

    def test_verified_feed_requires_identity(self):
        with self.assertRaises(ValueError):
            CoinDcxOptionsCaptureReadiness(documented_or_authorized_options_feed=True).validated()
        status = CoinDcxOptionsCaptureReadiness(
            documented_or_authorized_options_feed=True,
            endpoint_or_feed_id="verified-feed-id",
        ).status()
        self.assertEqual(status["options_pit_capture_status"], "READY_FOR_IMPLEMENTATION")

    def test_capture_gap_report_prioritizes_options_and_reports_context_boundary(self):
        report = capture_gap_report()
        self.assertEqual(report["primary_trade_instrument"], "OPTIONS")
        self.assertEqual(report["futures_context_role"], FUTURES_CONTEXT_ROLE)
        self.assertFalse(report["futures_may_satisfy_options_contract_quote"])
        self.assertIn(COINDCX_OPTIONS_PIT_DATASET, report["missing_collectors"])
        self.assertEqual(
            report["coindcx_options_capture_readiness"]["options_pit_capture_status"],
            "BLOCKED_NO_VERIFIED_DOCUMENTED_OR_AUTHORIZED_OPTIONS_FEED",
        )

    def test_architecture_never_allows_futures_or_model_substitution(self):
        contract = architecture_contract()
        self.assertEqual(contract["primary_trade_instrument"], "OPTIONS")
        self.assertTrue(contract["futures_context_only"])
        self.assertFalse(contract["futures_can_substitute_for_options_contract_or_quote"])
        self.assertFalse(contract["spot_can_substitute_for_options_contract_or_quote"])
        self.assertFalse(contract["model_price_can_substitute_for_observed_options_quote"])
        self.assertFalse(contract["undocumented_options_endpoint_reverse_engineering_allowed"])
        self.assertFalse(contract["futures_trade_generation_enabled"])
        self.assertFalse(contract["live_execution_enabled"])

        capture_contract = scheduler_contract()
        self.assertEqual(capture_contract["primary_trade_instrument"], "OPTIONS")
        self.assertEqual(capture_contract["futures_context_role"], FUTURES_CONTEXT_ROLE)
        self.assertFalse(capture_contract["futures_context_may_satisfy_options_quote"])
        self.assertFalse(capture_contract["futures_trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
