import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_onchain_capture import (
    BTC_ONCHAIN_DATASET,
    architecture_contract,
    glassnode_onchain_archive_record,
    onchain_context_from_pit_record,
    onchain_metric_from_pit_record,
)
from app.glassnode_btc_onchain_provider import GlassnodeMetricCapture, METRICS


def _t():
    return datetime(2026, 9, 5, 6, 30, tzinfo=timezone.utc)


def _capture(metric="MVRV"):
    spec = METRICS[metric]
    return GlassnodeMetricCapture(
        metric=metric,
        first_seen_at=_t(),
        provider_time=_t() - timedelta(hours=1),
        value=1.5 if metric in {"MVRV", "SOPR"} else 250.0,
        interval="1h",
        endpoint="https://api.glassnode.com" + spec["path"],
        unit=spec["unit"],
        historical_content_immutable=spec["pit_immutable"],
    ).validated()


class CryptoBtcOnchainCaptureTests(unittest.TestCase):
    def test_archive_preserves_provider_time_and_alphapilot_first_seen(self):
        record = glassnode_onchain_archive_record(_capture("MVRV"))
        self.assertEqual(record.dataset, BTC_ONCHAIN_DATASET)
        self.assertEqual(record.first_seen_at, _t())
        self.assertEqual(record.event_at, _t() - timedelta(hours=1))
        self.assertTrue(record.payload["historical_content_immutable"])
        self.assertFalse(record.payload["provider_delivery_time_proven"])
        self.assertTrue(record.payload["first_seen_required_for_click_replay"])

    def test_exchange_netflow_capture_preserves_mutable_history_flag(self):
        record = glassnode_onchain_archive_record(_capture("EXCHANGE_NETFLOW"))
        self.assertFalse(record.payload["historical_content_immutable"])
        self.assertFalse(record.payload["standalone_trade_signal"])

    def test_pit_record_converts_to_context_at_first_seen_not_provider_time(self):
        row = glassnode_onchain_archive_record(_capture("SOPR")).frozen_dict()
        metric = onchain_metric_from_pit_record(row)
        self.assertEqual(metric.observed_at, _t())
        self.assertEqual(metric.metadata["provider_time"], (_t() - timedelta(hours=1)).isoformat())
        context = onchain_context_from_pit_record(row)
        self.assertEqual(context.stance, "UNKNOWN")
        self.assertTrue(context.context_only)
        self.assertFalse(context.metadata["standalone_direction_allowed"])

    def test_exchange_and_whale_flow_semantics_never_create_standalone_direction(self):
        for metric_name in ("EXCHANGE_NETFLOW", "WHALE_EXCHANGE_FLOW"):
            context = onchain_context_from_pit_record(glassnode_onchain_archive_record(_capture(metric_name)).frozen_dict())
            self.assertEqual(context.stance, "UNKNOWN")
            self.assertTrue(context.context_only)
            self.assertEqual(context.metadata["role"], "FAST_EVENT")
            self.assertFalse(context.metadata["standalone_direction_allowed"])

    def test_contract_keeps_click_visibility_and_execution_fail_closed(self):
        contract = architecture_contract()
        self.assertTrue(contract["provider_time_separate_from_first_seen"])
        self.assertFalse(contract["pit_content_immutability_equals_exact_delivery_time"])
        self.assertFalse(contract["mutable_entity_metric_may_rewrite_first_seen"])
        self.assertFalse(contract["raw_onchain_metric_standalone_direction_allowed"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
