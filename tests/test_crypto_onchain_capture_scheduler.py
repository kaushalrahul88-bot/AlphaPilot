import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_onchain_capture import BTC_ONCHAIN_DATASET
from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_onchain_capture_scheduler import (
    CryptoOnchainCapturePolicy,
    CryptoOnchainPitCaptureScheduler,
    architecture_contract,
)
from app.glassnode_btc_onchain_provider import GlassnodeMetricCapture, METRICS


def _t(minute=30):
    return datetime(2026, 9, 5, 6, minute, tzinfo=timezone.utc)


class _ProviderPolicy:
    metrics = ("MVRV", "SOPR", "EXCHANGE_NETFLOW", "WHALE_EXCHANGE_FLOW")


class _Provider:
    policy = _ProviderPolicy()

    def __init__(self):
        self.calls = []

    def capture_metric(self, metric, *, first_seen_at):
        self.calls.append((metric, first_seen_at))
        spec = METRICS[metric]
        return GlassnodeMetricCapture(
            metric=metric,
            first_seen_at=first_seen_at,
            provider_time=_t() - timedelta(hours=1),
            value=1.5 if metric in {"MVRV", "SOPR"} else 250.0,
            interval="1h",
            endpoint="https://api.glassnode.com" + spec["path"],
            unit=spec["unit"],
            historical_content_immutable=spec["pit_immutable"],
        ).validated()


class _PartialProvider(_Provider):
    def capture_metric(self, metric, *, first_seen_at):
        if metric == "EXCHANGE_NETFLOW":
            raise RuntimeError("metric unavailable")
        return super().capture_metric(metric, first_seen_at=first_seen_at)


class CryptoOnchainCaptureSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_scheduler_makes_no_provider_or_store_call(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = CryptoOnchainPitCaptureScheduler(provider=provider, store=ledger)
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "CRYPTO_ONCHAIN_CAPTURE_DISABLED")
        self.assertEqual(provider.calls, [])
        self.assertEqual(ledger.manifest()["record_count"], 0)
        self.assertFalse(result["trade_generated"])

    async def test_enabled_scheduler_archives_all_configured_metrics(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = CryptoOnchainPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=CryptoOnchainCapturePolicy(enabled=True, poll_seconds=600),
        )
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "CRYPTO_ONCHAIN_CAPTURE_CYCLE_COMPLETE")
        self.assertEqual(len(result["captured"]), 4)
        self.assertEqual(ledger.manifest()["by_dataset"][BTC_ONCHAIN_DATASET], 4)
        self.assertTrue(all(not row["standalone_trade_signal"] for row in result["captured"]))
        self.assertFalse(result["trade_generated"])

    async def test_same_provider_bars_seen_later_are_idempotent(self):
        provider = _Provider()
        ledger = ImmutableBtcPitLedger()
        scheduler = CryptoOnchainPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=CryptoOnchainCapturePolicy(enabled=True),
        )
        first = await scheduler.run_cycle(now=_t())
        second = await scheduler.run_cycle(now=_t() + timedelta(minutes=10))
        self.assertTrue(all(row["storage_status"] == "INSERTED_FIRST_SEEN" for row in first["captured"]))
        self.assertTrue(all(row["storage_status"] == "IDEMPOTENT_DUPLICATE" for row in second["captured"]))
        self.assertEqual(ledger.manifest()["record_count"], 4)
        rows = ledger.visible_as_of(_t() + timedelta(hours=1), dataset=BTC_ONCHAIN_DATASET)
        self.assertTrue(all(row["first_seen_at"] == _t().isoformat() for row in rows))

    async def test_missing_metric_is_explicit_partial_failure_not_neutral(self):
        ledger = ImmutableBtcPitLedger()
        scheduler = CryptoOnchainPitCaptureScheduler(
            provider=_PartialProvider(),
            store=ledger,
            policy=CryptoOnchainCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle(now=_t())
        self.assertEqual(result["status"], "CRYPTO_ONCHAIN_CAPTURE_CYCLE_PARTIAL_FAILURE")
        error = [row for row in result["errors"] if row["metric"] == "EXCHANGE_NETFLOW"][0]
        self.assertFalse(error["missing_metric_treated_as_neutral"])
        self.assertFalse(result["trade_generated"])

    def test_policy_and_contract_fail_closed(self):
        with self.assertRaises(ValueError):
            CryptoOnchainCapturePolicy(enabled=True, poll_seconds=59).validated()
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertFalse(contract["scheduler_starts_at_import"])
        self.assertTrue(contract["pit_and_mutable_provider_metrics_both_preserve_first_seen"])
        self.assertFalse(contract["missing_metric_treated_as_neutral"])
        self.assertFalse(contract["raw_metric_assigns_direction"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
