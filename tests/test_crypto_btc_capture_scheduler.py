import unittest
from datetime import datetime, timedelta, timezone

from app.coindcx_btc_public_provider import CoinDcxFuturesRtCapture
from app.crypto_btc_capture_scheduler import (
    BtcCaptureSchedulerPolicy,
    BtcPitCaptureScheduler,
    COINDCX_FUTURES_RT_DATASET,
    COINDCX_FUTURES_RT_JOB,
    architecture_contract,
    capture_gap_report,
    coindcx_futures_rt_archive_record,
)
from app.crypto_btc_historical_data_adapter import HistoricalProvenance
from app.crypto_btc_pit_archive import ImmutableBtcPitLedger


def _t(minute=0):
    return datetime(2026, 9, 5, 6, minute, tzinfo=timezone.utc)


def _capture(first_seen_at=None):
    first_seen = first_seen_at or _t()
    return CoinDcxFuturesRtCapture(
        first_seen_at=first_seen,
        provider_snapshot_at=first_seen - timedelta(seconds=1),
        provider_tick_at=first_seen - timedelta(seconds=2),
        mark_price_at=first_seen - timedelta(seconds=1),
        funding_rate=0.0001,
        estimated_funding_rate=0.00011,
        mark_price=100000.0,
        last_price=100010.0,
        price_change_pct_24h=2.0,
        volume_24h=5000.0,
        market="BTCUSDT",
        raw_pair="B-BTC_USDT",
        provenance=HistoricalProvenance(
            provider="COINDCX",
            source_id=f"test:{int(first_seen.timestamp())}",
            availability_basis="FIRST_SEEN_CAPTURE",
            point_in_time_proven=True,
            immutable_archive=True,
            reconstructible_public_data=False,
        ),
    ).validated()


class _FakeProvider:
    def __init__(self):
        self.calls = 0

    def capture_futures_rt(self, *, first_seen_at):
        self.calls += 1
        return _capture(first_seen_at)


class _FailingProvider:
    def __init__(self):
        self.calls = 0

    def capture_futures_rt(self, *, first_seen_at):
        self.calls += 1
        raise RuntimeError("provider unavailable")


class CryptoBtcCaptureSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_scheduler_makes_no_provider_or_store_call(self):
        provider = _FakeProvider()
        ledger = ImmutableBtcPitLedger()
        scheduler = BtcPitCaptureScheduler(provider=provider, store=ledger)
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "BTC_CAPTURE_DISABLED")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(ledger.manifest()["record_count"], 0)
        self.assertFalse(result["provider_called"])
        self.assertFalse(result["store_written"])

    async def test_enabled_scheduler_archives_one_first_seen_funding_snapshot(self):
        provider = _FakeProvider()
        ledger = ImmutableBtcPitLedger()
        scheduler = BtcPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=BtcCaptureSchedulerPolicy(enabled=True, poll_seconds=60),
        )
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "BTC_CAPTURE_CYCLE_COMPLETE")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result["captured"][0]["dataset"], COINDCX_FUTURES_RT_DATASET)
        self.assertEqual(result["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(ledger.manifest()["record_count"], 1)
        self.assertFalse(result["options_trade_generated"])
        self.assertFalse(result["futures_trade_generated"])

    async def test_scheduler_does_not_repoll_before_interval(self):
        provider = _FakeProvider()
        ledger = ImmutableBtcPitLedger()
        scheduler = BtcPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=BtcCaptureSchedulerPolicy(enabled=True, poll_seconds=60),
        )
        await scheduler.run_cycle(now=_t())
        second = await scheduler.run_cycle(now=_t() + timedelta(seconds=30))
        self.assertEqual(provider.calls, 1)
        self.assertEqual(second["skipped"][0]["reason"], "NOT_DUE")
        self.assertEqual(ledger.manifest()["record_count"], 1)

    async def test_next_due_capture_uses_new_first_seen_record(self):
        provider = _FakeProvider()
        ledger = ImmutableBtcPitLedger()
        scheduler = BtcPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=BtcCaptureSchedulerPolicy(enabled=True, poll_seconds=60),
        )
        await scheduler.run_cycle(now=_t())
        await scheduler.run_cycle(now=_t() + timedelta(seconds=60))
        self.assertEqual(provider.calls, 2)
        self.assertEqual(ledger.manifest()["record_count"], 2)

    async def test_provider_failure_is_explicit_and_never_becomes_trade(self):
        provider = _FailingProvider()
        ledger = ImmutableBtcPitLedger()
        scheduler = BtcPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=BtcCaptureSchedulerPolicy(enabled=True, poll_seconds=60),
        )
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "BTC_CAPTURE_CYCLE_PARTIAL_FAILURE")
        self.assertEqual(result["errors"][0]["error_type"], "RuntimeError")
        self.assertEqual(ledger.manifest()["record_count"], 0)
        self.assertFalse(result["options_trade_generated"])
        self.assertFalse(result["futures_trade_generated"])

    def test_funding_archive_record_never_infers_oi_or_liquidations(self):
        record = coindcx_futures_rt_archive_record(_capture())
        self.assertEqual(record.dataset, COINDCX_FUTURES_RT_DATASET)
        self.assertIsNone(record.payload["open_interest"])
        self.assertIsNone(record.payload["liquidations"])
        self.assertFalse(record.payload["open_interest_inferred"])
        self.assertFalse(record.payload["liquidations_inferred"])

    def test_gap_report_keeps_unimplemented_critical_feeds_explicit(self):
        report = capture_gap_report()
        self.assertIn("COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES", report["missing_collectors"])
        self.assertIn("BTC_OPEN_INTEREST", report["missing_collectors"])
        self.assertIn("BTC_LIQUIDATIONS", report["missing_collectors"])
        self.assertFalse(report["missing_collectors_are_treated_as_neutral"])
        self.assertFalse(report["historical_options_fabricated"])

    def test_policy_is_fail_closed_for_unknown_job_and_too_fast_polling(self):
        with self.assertRaises(ValueError):
            BtcCaptureSchedulerPolicy(enabled=True, poll_seconds=9).validated()
        with self.assertRaises(ValueError):
            BtcCaptureSchedulerPolicy(enabled=True, enabled_jobs=("UNKNOWN",)).validated()

    def test_architecture_contract_keeps_collection_and_execution_off_by_default(self):
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertFalse(contract["scheduler_starts_at_import"])
        self.assertEqual(contract["implemented_jobs"], [COINDCX_FUTURES_RT_JOB])
        self.assertFalse(contract["options_trade_generation_allowed"])
        self.assertFalse(contract["futures_trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
