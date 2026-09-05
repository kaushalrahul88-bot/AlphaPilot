import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_deribit_options_capture_scheduler import (
    DeribitOptionsCapturePolicy,
    DeribitOptionsPitCaptureScheduler,
    architecture_contract,
)
from app.crypto_deribit_options_pit import DATASET
from app.deribit_btc_options_context_provider import DeribitBtcOptionsContextCapture


def _t(minutes=0):
    return datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


class _Provider:
    def __init__(self, times=None):
        self.times = list(times or [_t()])
        self.calls = 0

    def capture_context(self):
        seen = self.times[min(self.calls, len(self.times) - 1)]
        self.calls += 1
        return DeribitBtcOptionsContextCapture(
            first_seen_at=seen,
            underlying_price_usd=100_000.0,
            nearest_expiry_at=seen + timedelta(days=2),
            next_expiry_at=seen + timedelta(days=9),
            atm_mark_iv_pct=55.0 + self.calls,
            next_expiry_atm_mark_iv_pct=58.0 + self.calls,
            term_structure_slope_iv_points=3.0,
            total_call_open_interest_btc=10.0,
            total_put_open_interest_btc=12.0,
            put_call_open_interest_ratio=1.2,
            matched_contract_count=20,
            active_contract_count=22,
            valid_expiry_count=2,
        ).validated()


class _FailingProvider(_Provider):
    def capture_context(self):
        self.calls += 1
        raise RuntimeError("Deribit unavailable")


class CryptoDeribitOptionsCaptureSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_scheduler_makes_no_provider_or_store_call(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = DeribitOptionsPitCaptureScheduler(provider=provider, store=ledger)
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "DERIBIT_OPTIONS_CONTEXT_CAPTURE_DISABLED")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(ledger.manifest()["record_count"], 0)
        self.assertFalse(result["trade_generated"])

    async def test_enabled_scheduler_archives_global_context_only(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = DeribitOptionsPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=DeribitOptionsCapturePolicy(enabled=True, poll_seconds=300),
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "DERIBIT_OPTIONS_CONTEXT_CAPTURE_CYCLE_COMPLETE")
        self.assertEqual(result["captured"][0]["dataset"], DATASET)
        self.assertTrue(result["captured"][0]["global_options_context_only"])
        self.assertFalse(result["captured"][0]["coindcx_contract_selection_allowed"])
        self.assertFalse(result["captured"][0]["coindcx_quote_fill_allowed"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(ledger.manifest()["by_dataset"][DATASET], 1)

    async def test_each_current_snapshot_is_first_seen_at_its_own_capture_time(self):
        provider = _Provider(times=[_t(0), _t(5)])
        ledger = ImmutableBtcPitLedger()
        scheduler = DeribitOptionsPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=DeribitOptionsCapturePolicy(enabled=True),
        )
        await scheduler.run_cycle()
        await scheduler.run_cycle()
        rows = ledger.visible_as_of(_t(5), dataset=DATASET)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["first_seen_at"], _t(0).isoformat())
        self.assertEqual(rows[1]["first_seen_at"], _t(5).isoformat())

    async def test_provider_failure_is_explicit_not_neutralized(self):
        ledger = ImmutableBtcPitLedger()
        scheduler = DeribitOptionsPitCaptureScheduler(
            provider=_FailingProvider(),
            store=ledger,
            policy=DeribitOptionsCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "DERIBIT_OPTIONS_CONTEXT_CAPTURE_CYCLE_FAILURE")
        self.assertEqual(result["captured"], [])
        self.assertFalse(result["errors"][0]["missing_options_context_treated_as_neutral"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(ledger.manifest()["record_count"], 0)

    def test_policy_and_contract_fail_closed(self):
        with self.assertRaises(ValueError):
            DeribitOptionsCapturePolicy(enabled=True, poll_seconds=59).validated()
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertFalse(contract["scheduler_starts_at_import"])
        self.assertFalse(contract["instrument_metadata_polled_each_cycle"])
        self.assertTrue(contract["global_options_context_only"])
        self.assertFalse(contract["coindcx_contract_selection_allowed"])
        self.assertFalse(contract["coindcx_quote_fill_allowed"])
        self.assertFalse(contract["missing_options_context_treated_as_neutral"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
