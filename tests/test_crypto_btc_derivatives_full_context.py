import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_derivatives_capture import BTC_LIQUIDATIONS_DATASET, BTC_OPEN_INTEREST_DATASET
from app.crypto_btc_derivatives_evidence import derivatives_evidence_from_full_pit_context
from app.crypto_btc_funding_percentile import DATASET as FUNDING_DATASET, FundingPercentilePolicy


def _t():
    return datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)


def _base_rows():
    event = _t() - timedelta(hours=4)
    return [
        {
            "dataset": BTC_OPEN_INTEREST_DATASET,
            "first_seen_at": _t().isoformat(),
            "event_at": event.isoformat(),
            "payload": {"interval": "4h", "open_interest_open_usd": 100.0, "open_interest_close_usd": 110.0},
        },
        {
            "dataset": BTC_LIQUIDATIONS_DATASET,
            "first_seen_at": _t().isoformat(),
            "event_at": event.isoformat(),
            "payload": {"interval": "4h", "long_liquidation_usd": 1_000_000.0, "short_liquidation_usd": 2_000_000.0},
        },
    ]


class CryptoBtcDerivativesFullContextTests(unittest.TestCase):
    def test_prior_funding_history_is_added_and_extreme_crowding_suppresses_chase(self):
        rows = _base_rows()
        for i in range(6):
            rows.append({
                "dataset": FUNDING_DATASET,
                "first_seen_at": (_t() - timedelta(hours=6 - i)).isoformat(),
                "source_key": f"funding-{i}",
                "payload": {"funding_rate": float(i)},
            })
        evidence = derivatives_evidence_from_full_pit_context(
            rows,
            decision_at=_t(),
            price_change_pct=2.0,
            funding_policy=FundingPercentilePolicy(min_prior_samples=5, lookback_days=2),
        )
        self.assertEqual(evidence.metadata["funding_context_status"], "FUNDING_PERCENTILE_READY")
        self.assertEqual(evidence.metadata["funding_percentile_point_in_time"], 1.0)
        self.assertEqual(evidence.stance, "NEUTRAL")
        self.assertTrue(evidence.metadata["crowded_long"])
        self.assertFalse(evidence.metadata["may_generate_futures_trade"])

    def test_insufficient_funding_history_does_not_invent_percentile_or_block_valid_oi_liquidation_state(self):
        rows = _base_rows() + [{
            "dataset": FUNDING_DATASET,
            "first_seen_at": (_t() - timedelta(minutes=1)).isoformat(),
            "source_key": "funding-only",
            "payload": {"funding_rate": 0.0001},
        }]
        evidence = derivatives_evidence_from_full_pit_context(
            rows,
            decision_at=_t(),
            price_change_pct=2.0,
            funding_policy=FundingPercentilePolicy(min_prior_samples=5, lookback_days=2),
        )
        self.assertEqual(evidence.metadata["funding_context_status"], "INSUFFICIENT_FUNDING_HISTORY")
        self.assertIsNone(evidence.metadata["funding_percentile_point_in_time"])
        self.assertEqual(evidence.stance, "BULLISH")


if __name__ == "__main__":
    unittest.main()
