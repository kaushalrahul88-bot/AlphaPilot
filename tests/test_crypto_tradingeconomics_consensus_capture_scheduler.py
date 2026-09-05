import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_macro_event_intelligence import MacroConsensusSnapshot
from app.crypto_tradingeconomics_consensus_capture_scheduler import (
    TradingEconomicsConsensusCapturePolicy,
    TradingEconomicsConsensusPitCaptureScheduler,
    architecture_contract,
)
from app.tradingeconomics_macro_consensus_provider import TradingEconomicsConsensusTarget


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
CYCLE = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def _target():
    return TradingEconomicsConsensusTarget(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        reference_period="2026-08",
        expected_release_at=RELEASE,
    )


def _snapshot(*, headline=0.3, core=0.2, provider_minute=59, seen_second=1):
    return MacroConsensusSnapshot(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        release_at=RELEASE,
        provider_time=datetime(2026, 9, 11, 11, provider_minute, tzinfo=timezone.utc),
        first_seen_at=CYCLE + timedelta(seconds=seen_second),
        source_name="TRADING_ECONOMICS",
        source_ref="https://api.tradingeconomics.com/calendar/example#calendar_ids=1,2",
        values={"headline_mom_pct": headline, "core_mom_pct": core},
        units={"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"},
        source_verified=True,
    ).validated()


class _Provider:
    def __init__(self, snapshots=None, error=None):
        self.snapshots = list(snapshots or [])
        self.error = error
        self.calls = []

    def fetch_consensus(self, *, target):
        self.calls.append(target.event_key)
        if self.error is not None:
            raise self.error
        if not self.snapshots:
            raise RuntimeError("no fake snapshot")
        if len(self.snapshots) == 1:
            return self.snapshots[0]
        return self.snapshots.pop(0)


class TradingEconomicsConsensusSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_scheduler_makes_no_provider_call(self):
        provider = _Provider([_snapshot()])
        scheduler = TradingEconomicsConsensusPitCaptureScheduler(
            provider=provider,
            store=ImmutableBtcPitLedger(),
            targets=[_target()],
        )
        result = await scheduler.run_cycle(now=CYCLE)
        self.assertEqual(result["status"], "TRADING_ECONOMICS_CONSENSUS_CAPTURE_DISABLED")
        self.assertFalse(result["provider_called"])
        self.assertEqual(provider.calls, [])

    async def test_pre_release_consensus_is_inserted(self):
        provider = _Provider([_snapshot()])
        store = ImmutableBtcPitLedger()
        scheduler = TradingEconomicsConsensusPitCaptureScheduler(
            provider=provider,
            store=store,
            targets=[_target()],
            policy=TradingEconomicsConsensusCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle(now=CYCLE)
        self.assertEqual(result["status"], "TRADING_ECONOMICS_CONSENSUS_CAPTURE_CYCLE_COMPLETE")
        self.assertTrue(result["provider_called"])
        self.assertTrue(result["store_written"])
        self.assertEqual(result["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertFalse(result["captured"][0]["numeric_surprise_generated"])
        self.assertFalse(result["captured"][0]["direction_assigned"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(len(store.visible_as_of(CYCLE + timedelta(minutes=1))), 1)

    async def test_unchanged_repoll_is_idempotent_even_with_later_first_seen(self):
        first = _snapshot(seen_second=1)
        second = _snapshot(seen_second=20)
        provider = _Provider([first, second])
        store = ImmutableBtcPitLedger()
        scheduler = TradingEconomicsConsensusPitCaptureScheduler(
            provider=provider,
            store=store,
            targets=[_target()],
            policy=TradingEconomicsConsensusCapturePolicy(enabled=True),
        )
        one = await scheduler.run_cycle(now=CYCLE)
        two = await scheduler.run_cycle(now=CYCLE + timedelta(seconds=15))
        self.assertEqual(one["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(two["captured"][0]["storage_status"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(scheduler.idempotent_duplicates, 1)
        self.assertEqual(len(store.manifest()["by_dataset"]), 1)
        self.assertEqual(store.manifest()["record_count"], 1)

    async def test_changed_pre_release_consensus_creates_new_immutable_state(self):
        provider = _Provider([
            _snapshot(headline=0.3, core=0.2, provider_minute=58, seen_second=1),
            _snapshot(headline=0.4, core=0.2, provider_minute=59, seen_second=20),
        ])
        store = ImmutableBtcPitLedger()
        scheduler = TradingEconomicsConsensusPitCaptureScheduler(
            provider=provider,
            store=store,
            targets=[_target()],
            policy=TradingEconomicsConsensusCapturePolicy(enabled=True),
        )
        one = await scheduler.run_cycle(now=CYCLE)
        two = await scheduler.run_cycle(now=CYCLE + timedelta(seconds=15))
        self.assertEqual(one["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(two["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertNotEqual(one["captured"][0]["state_hash"], two["captured"][0]["state_hash"])
        self.assertEqual(store.manifest()["record_count"], 2)

    async def test_target_is_closed_at_release_without_provider_call(self):
        provider = _Provider([_snapshot()])
        scheduler = TradingEconomicsConsensusPitCaptureScheduler(
            provider=provider,
            store=ImmutableBtcPitLedger(),
            targets=[_target()],
            policy=TradingEconomicsConsensusCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle(now=RELEASE)
        self.assertFalse(result["provider_called"])
        self.assertEqual(provider.calls, [])
        self.assertEqual(result["closed_targets"][0]["event_key"], _target().event_key)
        self.assertFalse(result["closed_targets"][0]["post_release_consensus_captured"])

    async def test_provider_failure_writes_nothing_and_is_not_neutralized(self):
        provider = _Provider(error=ValueError("missing representative consensus"))
        store = ImmutableBtcPitLedger()
        scheduler = TradingEconomicsConsensusPitCaptureScheduler(
            provider=provider,
            store=store,
            targets=[_target()],
            policy=TradingEconomicsConsensusCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle(now=CYCLE)
        self.assertEqual(result["status"], "TRADING_ECONOMICS_CONSENSUS_CAPTURE_CYCLE_PARTIAL_FAILURE")
        self.assertFalse(result["store_written"])
        self.assertEqual(store.manifest()["record_count"], 0)
        self.assertFalse(result["errors"][0]["missing_consensus_treated_as_neutral"])
        self.assertFalse(result["errors"][0]["post_release_consensus_backfilled"])

    def test_duplicate_target_event_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            TradingEconomicsConsensusPitCaptureScheduler(
                provider=_Provider(),
                store=ImmutableBtcPitLedger(),
                targets=[_target(), _target()],
            )

    def test_poll_interval_cannot_be_excessively_aggressive(self):
        with self.assertRaises(ValueError):
            TradingEconomicsConsensusCapturePolicy(enabled=True, poll_seconds=59).validated()

    def test_architecture_is_pre_release_only_and_trade_separated(self):
        contract = architecture_contract()
        self.assertTrue(contract["exact_unchanged_repoll_is_idempotent"])
        self.assertTrue(contract["changed_pre_release_consensus_is_new_state"])
        self.assertTrue(contract["target_closes_at_official_release"])
        self.assertFalse(contract["provider_called_after_target_release"])
        self.assertFalse(contract["post_release_consensus_backfill_allowed"])
        self.assertFalse(contract["missing_consensus_treated_as_neutral"])
        self.assertFalse(contract["numeric_surprise_generated"])
        self.assertFalse(contract["direction_assigned"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
