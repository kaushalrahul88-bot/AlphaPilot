import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_deribit_options_evidence import (
    DeribitOptionsEvidencePolicy,
    architecture_contract,
    deribit_options_evidence_from_pit_records,
)
from app.crypto_deribit_options_pit import DATASET


def _t(minutes=0):
    return datetime(2026, 9, 5, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _row(seen, iv, ratio=1.0, slope=2.0, key=None):
    return {
        "dataset": DATASET,
        "first_seen_at": seen.isoformat(),
        "source_key": key or f"row-{seen.isoformat()}",
        "payload": {
            "atm_mark_iv_pct": iv,
            "put_call_open_interest_ratio": ratio,
            "term_structure_slope_iv_points": slope,
            "global_options_context_only": True,
        },
    }


class CryptoDeribitOptionsEvidenceTests(unittest.TestCase):
    def test_no_visible_snapshot_is_unknown_context(self):
        evidence = deribit_options_evidence_from_pit_records([], decision_at=_t())
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertEqual(evidence.metadata["status"], "NO_VISIBLE_DERIBIT_OPTIONS_CONTEXT")
        self.assertFalse(evidence.metadata["coindcx_contract_selection_allowed"])

    def test_future_snapshot_is_excluded(self):
        evidence = deribit_options_evidence_from_pit_records(
            [_row(_t(1), 80.0)], decision_at=_t()
        )
        self.assertEqual(evidence.metadata["status"], "NO_VISIBLE_DERIBIT_OPTIONS_CONTEXT")

    def test_insufficient_prior_iv_history_leaves_percentile_unknown(self):
        rows = [
            _row(_t(-10), 50.0),
            _row(_t(-5), 55.0),
            _row(_t(0), 60.0, ratio=1.5, slope=3.0),
        ]
        evidence = deribit_options_evidence_from_pit_records(
            rows,
            decision_at=_t(0),
            policy=DeribitOptionsEvidencePolicy(min_prior_iv_samples=5),
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertFalse(evidence.metadata["iv_percentile_ready"])
        self.assertIsNone(evidence.metadata["iv_percentile_point_in_time"])
        self.assertEqual(evidence.metadata["prior_iv_sample_count"], 2)
        self.assertIn("PUT_OI_HEAVY", evidence.metadata["tags"])

    def test_iv_percentile_uses_only_prior_visible_snapshots(self):
        rows = [
            _row(_t(-50), 40.0),
            _row(_t(-40), 50.0),
            _row(_t(-30), 60.0),
            _row(_t(-20), 70.0),
            _row(_t(-10), 80.0),
            _row(_t(0), 65.0, ratio=0.8, slope=-4.0),
            _row(_t(1), 10.0, ratio=9.0, slope=99.0),
        ]
        evidence = deribit_options_evidence_from_pit_records(
            rows,
            decision_at=_t(0),
            policy=DeribitOptionsEvidencePolicy(min_prior_iv_samples=5),
        )
        self.assertEqual(evidence.metadata["prior_iv_sample_count"], 5)
        self.assertAlmostEqual(evidence.metadata["iv_percentile_point_in_time"], 3 / 5)
        self.assertEqual(evidence.metadata["put_call_open_interest_ratio"], 0.8)
        self.assertEqual(evidence.metadata["term_structure_slope_iv_points"], -4.0)
        self.assertFalse(evidence.metadata["future_rows_used"])

    def test_high_iv_percentile_can_tag_context_but_never_direction(self):
        rows = [_row(_t(-60 + i * 10), 40.0 + i) for i in range(5)]
        rows.append(_row(_t(0), 90.0, ratio=1.0, slope=8.0))
        evidence = deribit_options_evidence_from_pit_records(
            rows,
            decision_at=_t(0),
            policy=DeribitOptionsEvidencePolicy(min_prior_iv_samples=5),
        )
        self.assertEqual(evidence.metadata["iv_percentile_point_in_time"], 1.0)
        self.assertIn("IV_EXTREME_HIGH", evidence.metadata["tags"])
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertFalse(evidence.metadata["coindcx_contract_selection_allowed"])
        self.assertFalse(evidence.metadata["coindcx_quote_fill_allowed"])
        self.assertFalse(evidence.metadata["coindcx_pnl_replay_allowed"])
        self.assertFalse(evidence.metadata["trade_generated"])

    def test_stale_snapshot_fails_closed(self):
        evidence = deribit_options_evidence_from_pit_records(
            [_row(_t(-20), 55.0)],
            decision_at=_t(0),
            policy=DeribitOptionsEvidencePolicy(max_snapshot_age_seconds=15 * 60),
        )
        self.assertEqual(evidence.metadata["status"], "STALE_DERIBIT_OPTIONS_CONTEXT")
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_25_delta_skew_is_not_fabricated(self):
        evidence = deribit_options_evidence_from_pit_records(
            [_row(_t(-1), 55.0)],
            decision_at=_t(0),
            policy=DeribitOptionsEvidencePolicy(min_prior_iv_samples=2),
        )
        self.assertIsNone(evidence.metadata["put_call_skew_25d"])
        self.assertIsNone(evidence.metadata["skew_25d"])
        self.assertFalse(evidence.metadata["skew_25d_inferred"])

    def test_policy_and_contract_keep_context_nonexecuting(self):
        with self.assertRaises(ValueError):
            DeribitOptionsEvidencePolicy(min_prior_iv_samples=1).validated()
        with self.assertRaises(ValueError):
            DeribitOptionsEvidencePolicy(iv_lookback_days=0).validated()
        contract = architecture_contract()
        self.assertTrue(contract["uses_only_visible_first_seen_snapshots"])
        self.assertTrue(contract["iv_percentile_uses_only_prior_visible_history"])
        self.assertFalse(contract["insufficient_iv_history_invents_percentile"])
        self.assertFalse(contract["skew_25d_inferred"])
        self.assertFalse(contract["underlying_direction_vote_allowed"])
        self.assertFalse(contract["coindcx_contract_selection_allowed"])
        self.assertFalse(contract["coindcx_quote_fill_allowed"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
