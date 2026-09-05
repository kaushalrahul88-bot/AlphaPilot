import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_stablecoin_liquidity import (
    StablecoinLiquidityPolicy,
    aggregate_stablecoin_liquidity_context,
    architecture_contract,
)
from app.crypto_stablecoin_pit_capture import STABLECOIN_SUPPLY_DATASET


def _t():
    return datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _row(seen_at, total, key):
    return {
        "dataset": STABLECOIN_SUPPLY_DATASET,
        "first_seen_at": seen_at.isoformat(),
        "source_key": key,
        "payload": {"total_circulating": total},
    }


class CryptoStablecoinLiquidityTests(unittest.TestCase):
    def test_no_visible_snapshot_is_unknown_context(self):
        evidence = aggregate_stablecoin_liquidity_context([], decision_at=_t())
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertEqual(evidence.metadata["liquidity_state"], "UNKNOWN")
        self.assertFalse(evidence.metadata["may_generate_trade"])

    def test_future_snapshot_is_excluded(self):
        evidence = aggregate_stablecoin_liquidity_context(
            [_row(_t() + timedelta(minutes=1), 100.0, "future")],
            decision_at=_t(),
        )
        self.assertEqual(evidence.metadata["excluded_future_rows"], 1)
        self.assertEqual(evidence.metadata["liquidity_state"], "UNKNOWN")
        self.assertEqual(evidence.stance, "UNKNOWN")

    def test_expanding_supply_is_context_not_bullish_vote(self):
        rows = [
            _row(_t() - timedelta(hours=25), 100.0, "prior"),
            _row(_t() - timedelta(minutes=30), 102.0, "latest"),
        ]
        evidence = aggregate_stablecoin_liquidity_context(rows, decision_at=_t())
        self.assertEqual(evidence.metadata["liquidity_state"], "EXPANDING")
        self.assertGreater(evidence.metadata["supply_change_pct"], 0)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertFalse(evidence.metadata["aggregate_supply_equals_exchange_inflow"])
        self.assertFalse(evidence.metadata["aggregate_supply_equals_deployable_spot_buying_power"])

    def test_contracting_supply_is_context_not_bearish_vote(self):
        rows = [
            _row(_t() - timedelta(hours=25), 100.0, "prior"),
            _row(_t() - timedelta(minutes=30), 98.0, "latest"),
        ]
        evidence = aggregate_stablecoin_liquidity_context(rows, decision_at=_t())
        self.assertEqual(evidence.metadata["liquidity_state"], "CONTRACTING")
        self.assertLess(evidence.metadata["supply_change_pct"], 0)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_small_change_is_stable(self):
        rows = [
            _row(_t() - timedelta(hours=25), 100.0, "prior"),
            _row(_t() - timedelta(minutes=30), 100.05, "latest"),
        ]
        evidence = aggregate_stablecoin_liquidity_context(rows, decision_at=_t())
        self.assertEqual(evidence.metadata["liquidity_state"], "STABLE")
        self.assertEqual(evidence.strength, "LOW")
        self.assertEqual(evidence.stance, "UNKNOWN")

    def test_insufficient_prior_history_does_not_invent_change(self):
        evidence = aggregate_stablecoin_liquidity_context(
            [_row(_t() - timedelta(minutes=30), 100.0, "latest")],
            decision_at=_t(),
        )
        self.assertEqual(evidence.metadata["liquidity_state"], "UNKNOWN")
        self.assertNotIn("supply_change_pct", evidence.metadata)
        self.assertEqual(evidence.stance, "UNKNOWN")

    def test_stale_snapshot_fails_closed(self):
        rows = [
            _row(_t() - timedelta(hours=30), 100.0, "prior"),
            _row(_t() - timedelta(hours=3), 101.0, "latest"),
        ]
        evidence = aggregate_stablecoin_liquidity_context(
            rows,
            decision_at=_t(),
            policy=StablecoinLiquidityPolicy(max_snapshot_age_seconds=2 * 60 * 60),
        )
        self.assertEqual(evidence.metadata["liquidity_state"], "UNKNOWN")
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertIn("stale", evidence.reason.lower())

    def test_policy_and_contract_are_fail_closed(self):
        with self.assertRaises(ValueError):
            StablecoinLiquidityPolicy(comparison_hours=0).validated()
        with self.assertRaises(ValueError):
            StablecoinLiquidityPolicy(stable_band_pct=-0.1).validated()
        contract = architecture_contract()
        self.assertTrue(contract["point_in_time_first_seen_only"])
        self.assertFalse(contract["aggregate_supply_may_emit_bullish_bearish_stance"])
        self.assertTrue(contract["venue_specific_flow_is_separate_dataset"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
