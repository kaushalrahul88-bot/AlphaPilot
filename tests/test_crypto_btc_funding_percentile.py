import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_funding_percentile import (
    DATASET,
    FundingPercentilePolicy,
    architecture_contract,
    funding_percentile_from_pit_records,
)


def _t(minute=0):
    return datetime(2026, 9, 5, 6, minute, tzinfo=timezone.utc)


def _row(seen_at, rate, source_key):
    return {
        "dataset": DATASET,
        "first_seen_at": seen_at.isoformat(),
        "source_key": source_key,
        "payload": {"funding_rate": rate},
    }


class CryptoBtcFundingPercentileTests(unittest.TestCase):
    def test_percentile_uses_only_prior_visible_history(self):
        base = _t() - timedelta(hours=21)
        rows = [_row(base + timedelta(hours=i), float(i), f"r{i}") for i in range(21)]
        result = funding_percentile_from_pit_records(
            rows,
            decision_at=_t(),
            policy=FundingPercentilePolicy(min_prior_samples=20, lookback_days=2),
        )
        self.assertEqual(result["status"], "FUNDING_PERCENTILE_READY")
        self.assertEqual(result["current_rate"], 20.0)
        self.assertEqual(result["prior_sample_count"], 20)
        self.assertEqual(result["percentile"], 1.0)
        self.assertTrue(result["point_in_time_only"])

    def test_future_snapshot_is_excluded(self):
        rows = [_row(_t() - timedelta(minutes=10 - i), float(i), f"r{i}") for i in range(10)]
        rows.append(_row(_t() + timedelta(seconds=1), 999.0, "future"))
        result = funding_percentile_from_pit_records(
            rows,
            decision_at=_t(),
            policy=FundingPercentilePolicy(min_prior_samples=5, lookback_days=1),
        )
        self.assertEqual(result["status"], "FUNDING_PERCENTILE_READY")
        self.assertNotEqual(result["current_rate"], 999.0)
        self.assertEqual(result["excluded_future_rows"], 1)

    def test_insufficient_history_returns_no_percentile(self):
        rows = [_row(_t() - timedelta(minutes=5 - i), float(i), f"r{i}") for i in range(5)]
        result = funding_percentile_from_pit_records(
            rows,
            decision_at=_t(),
            policy=FundingPercentilePolicy(min_prior_samples=5, lookback_days=1),
        )
        self.assertEqual(result["status"], "INSUFFICIENT_FUNDING_HISTORY")
        self.assertIsNone(result["percentile"])
        self.assertFalse(result["may_generate_trade"])

    def test_invalid_funding_rows_are_ignored(self):
        rows = [
            _row(_t() - timedelta(minutes=2), "bad", "bad"),
            _row(_t() - timedelta(minutes=1), 0.001, "good"),
        ]
        result = funding_percentile_from_pit_records(
            rows,
            decision_at=_t(),
            policy=FundingPercentilePolicy(min_prior_samples=5, lookback_days=1),
        )
        self.assertEqual(result["excluded_invalid_rows"], 1)
        self.assertEqual(result["current_rate"], 0.001)

    def test_policy_is_fail_closed(self):
        with self.assertRaises(ValueError):
            FundingPercentilePolicy(min_prior_samples=4).validated()
        with self.assertRaises(ValueError):
            FundingPercentilePolicy(lookback_days=0).validated()

    def test_architecture_contract_keeps_percentile_context_only(self):
        contract = architecture_contract()
        self.assertTrue(contract["percentile_uses_only_prior_first_seen_history"])
        self.assertTrue(contract["insufficient_history_fails_closed"])
        self.assertTrue(contract["percentile_is_market_context_only"])
        self.assertFalse(contract["futures_trade_generation_allowed"])
        self.assertFalse(contract["options_trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
