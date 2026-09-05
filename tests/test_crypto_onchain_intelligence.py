import unittest
from datetime import datetime, timezone

from app.crypto_onchain_intelligence import (
    OnchainMetric,
    generic_metric_context,
    metric_semantics,
    onchain_architecture_contract,
    token_unlock_context,
)


def _now():
    return datetime(2026, 9, 5, 3, 30, tzinfo=timezone.utc)


class CryptoOnchainIntelligenceTests(unittest.TestCase):
    def test_mvrv_and_sopr_are_context_not_intraday_triggers(self):
        for name in ("MVRV", "SOPR"):
            semantics = metric_semantics(name)
            self.assertFalse(semantics["standalone_direction_allowed"])
            row = generic_metric_context(
                OnchainMetric(
                    asset="BTC",
                    metric=name,
                    observed_at=_now(),
                    value=1.2,
                    source="ONCHAIN_PROVIDER",
                    role=semantics["role"],
                    historical_percentile=0.92,
                )
            )
            self.assertTrue(row.context_only)
            self.assertEqual(row.stance, "UNKNOWN")

    def test_network_activity_and_miner_flow_are_not_automatic_direction_votes(self):
        for name in ("ACTIVE_ADDRESSES", "MINER_EXCHANGE_FLOW"):
            semantics = metric_semantics(name)
            row = generic_metric_context(
                OnchainMetric(
                    asset="BTC",
                    metric=name,
                    observed_at=_now(),
                    value=1000,
                    source="CHAIN",
                    role=semantics["role"],
                )
            )
            self.assertTrue(row.context_only)
            self.assertFalse(row.metadata["standalone_direction_allowed"])

    def test_large_token_unlock_is_supply_context_not_automatic_put_or_short(self):
        row = token_unlock_context(
            asset="SOL",
            observed_at=_now(),
            unlock_pct_circulating=12.0,
            source="TOKENOMICS_SOURCE",
            recipient_type="EARLY_INVESTORS",
        )
        self.assertTrue(row.metadata["large_unlock"])
        self.assertEqual(row.stance, "UNKNOWN")
        self.assertTrue(row.context_only)

    def test_onchain_contract_forbids_slow_metric_intraday_triggering(self):
        contract = onchain_architecture_contract()
        self.assertFalse(contract["raw_transfer_equals_trade"])
        self.assertFalse(contract["slow_cycle_metric_may_trigger_intraday_trade"])
        self.assertTrue(contract["asset_specific_interpretation_required"])
        self.assertFalse(contract["broker_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
