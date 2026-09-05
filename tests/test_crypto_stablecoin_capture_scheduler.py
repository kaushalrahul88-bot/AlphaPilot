import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_stablecoin_capture_scheduler import (
    StablecoinSupplyCapturePolicy,
    StablecoinSupplyPitCaptureScheduler,
    architecture_contract,
)
from app.crypto_stablecoin_pit_capture import STABLECOIN_SUPPLY_DATASET
from app.defillama_stablecoin_provider import DefiLlamaStablecoinSupplyCapture


def _t(minute=30):
    return datetime(2026, 9, 5, 6, minute, tzinfo=timezone.utc)


class _Provider:
    def __init__(self, total=150.0):
        self.calls = []
        self.total = total

    def capture_supply(self, *, first_seen_at):
        self.calls.append(first_seen_at)
        return DefiLlamaStablecoinSupplyCapture(
            first_seen_at=first_seen_at,
            peg_type="peggedUSD",
            total_circulating=self.total,
            by_symbol={"USDC": 50.0, "USDT": self.total - 50.0},
            prices={"USDC": 1.0, "USDT": 1.0},
            asset_count=2,
        ).validated()


class _FailingProvider(_Provider):
    def capture_supply(self, *, first_seen_at):
        self.calls.append(first_seen_at)
        raise RuntimeError("stablecoin endpoint unavailable")


class CryptoStablecoinCaptureSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_scheduler_makes_no_provider_or_store_call(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = StablecoinSupplyPitCaptureScheduler(provider=provider, store=ledger)
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "STABLECOIN_SUPPLY_CAPTURE_DISABLED")
        self.assertEqual(provider.calls, [])
        self.assertEqual(ledger.manifest()["record_count"], 0)
        self.assertFalse(result["trade_generated"])

    async def test_enabled_scheduler_archives_first_seen_snapshot_without_direction(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = StablecoinSupplyPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=StablecoinSupplyCapturePolicy(enabled=True, poll_seconds=600),
        )
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "STABLECOIN_SUPPLY_CAPTURE_CYCLE_COMPLETE")
        self.assertEqual(len(result["captured"]), 1)
        self.assertEqual(result["captured"][0]["dataset"], STABLECOIN_SUPPLY_DATASET)
        self.assertFalse(result["captured"][0]["aggregate_supply_equals_exchange_inflow"])
        self.assertFalse(result["captured"][0]["direction_assigned"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(ledger.manifest()["by_dataset"][STABLECOIN_SUPPLY_DATASET], 1)

    async def test_later_poll_is_new_first_seen_snapshot_not_backdated_history(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = StablecoinSupplyPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=StablecoinSupplyCapturePolicy(enabled=True, poll_seconds=600),
        )
        first = await scheduler.run_cycle(now=_t())
        second = await scheduler.run_cycle(now=_t(40))
        self.assertEqual(first["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(second["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        rows = ledger.visible_as_of(_t(40), dataset=STABLECOIN_SUPPLY_DATASET)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["first_seen_at"], _t().isoformat())
        self.assertEqual(rows[1]["first_seen_at"], _t(40).isoformat())

    async def test_provider_failure_is_explicit_and_not_neutralized(self):
        ledger = ImmutableBtcPitLedger()
        scheduler = StablecoinSupplyPitCaptureScheduler(
            provider=_FailingProvider(),
            store=ledger,
            policy=StablecoinSupplyCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "STABLECOIN_SUPPLY_CAPTURE_CYCLE_FAILURE")
        self.assertEqual(result["captured"], [])
        self.assertFalse(result["errors"][0]["missing_supply_treated_as_neutral"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(ledger.manifest()["record_count"], 0)

    def test_policy_and_contract_fail_closed(self):
        with self.assertRaises(ValueError):
            StablecoinSupplyCapturePolicy(enabled=True, poll_seconds=299).validated()
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertFalse(contract["scheduler_starts_at_import"])
        self.assertTrue(contract["aggregate_supply_only"])
        self.assertFalse(contract["venue_specific_exchange_flow_captured"])
        self.assertFalse(contract["missing_supply_treated_as_neutral"])
        self.assertFalse(contract["direction_assigned"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
