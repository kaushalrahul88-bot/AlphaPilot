import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_deribit_options_evidence import DeribitOptionsEvidencePolicy, deribit_options_evidence_from_pit_records
from app.crypto_deribit_options_greeks_pit import DATASET as GREEKS_DATASET
from app.crypto_deribit_options_pit import DATASET as CONTEXT_DATASET


def _t(seconds=0):
    return datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def _context(seen, *, iv=60.0, ratio=1.2, slope=2.0):
    return {
        "dataset": CONTEXT_DATASET,
        "first_seen_at": seen.isoformat(),
        "payload": {
            "atm_mark_iv_pct": iv,
            "put_call_open_interest_ratio": ratio,
            "term_structure_slope_iv_points": slope,
            "global_options_context_only": True,
            "coindcx_contract_data": False,
        },
    }


def _greeks(seen, *, skew=6.0):
    return {
        "dataset": GREEKS_DATASET,
        "first_seen_at": seen.isoformat(),
        "payload": {
            "put_call_skew_25d_iv_points": skew,
            "skew_25d_observed_from_ticker_delta": True,
            "skew_25d_inferred_from_strike": False,
            "call": {"instrument_name": "BTC-C25", "delta": 0.24, "mark_iv_pct": 52.0},
            "put": {"instrument_name": "BTC-P25", "delta": -0.26, "mark_iv_pct": 58.0},
            "global_options_context_only": True,
            "coindcx_contract_data": False,
        },
    }


class CryptoDeribitOptionsEvidenceGreeksTests(unittest.TestCase):
    def test_fresh_chain_and_fresh_observed_delta_greeks_are_combined_as_context_only(self):
        records = [_context(_t(-5)), _greeks(_t(-2), skew=6.0)]
        evidence = deribit_options_evidence_from_pit_records(
            records,
            decision_at=_t(),
            policy=DeribitOptionsEvidencePolicy(min_prior_iv_samples=2, max_snapshot_age_seconds=60, max_greeks_age_seconds=10),
        )
        self.assertEqual(evidence.metadata["status"], "DERIBIT_OPTIONS_CONTEXT_READY")
        self.assertEqual(evidence.metadata["chain_context_status"], "READY")
        self.assertEqual(evidence.metadata["greeks_status"], "READY")
        self.assertEqual(evidence.metadata["skew_25d"], 6.0)
        self.assertTrue(evidence.metadata["skew_25d_observed_from_ticker_delta"])
        self.assertFalse(evidence.metadata["skew_25d_inferred"])
        self.assertFalse(evidence.metadata["skew_25d_inferred_from_strike"])
        self.assertEqual(evidence.metadata["greeks_call_instrument"], "BTC-C25")
        self.assertEqual(evidence.metadata["greeks_put_instrument"], "BTC-P25")
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertFalse(evidence.metadata["coindcx_contract_selection_allowed"])
        self.assertFalse(evidence.metadata["coindcx_quote_fill_allowed"])
        self.assertFalse(evidence.metadata["coindcx_pnl_replay_allowed"])
        self.assertFalse(evidence.metadata["trade_generated"])

    def test_fresh_chain_with_stale_greeks_keeps_chain_and_drops_skew(self):
        evidence = deribit_options_evidence_from_pit_records(
            [_context(_t(-5)), _greeks(_t(-20), skew=9.0)],
            decision_at=_t(),
            policy=DeribitOptionsEvidencePolicy(min_prior_iv_samples=2, max_snapshot_age_seconds=60, max_greeks_age_seconds=10),
        )
        self.assertEqual(evidence.metadata["chain_context_status"], "READY")
        self.assertEqual(evidence.metadata["greeks_status"], "STALE")
        self.assertIsNone(evidence.metadata["skew_25d"])
        self.assertFalse(evidence.metadata["skew_25d_observed_from_ticker_delta"])
        self.assertEqual(evidence.metadata["put_call_open_interest_ratio"], 1.2)
        self.assertEqual(evidence.metadata["term_structure_slope_iv_points"], 2.0)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_future_greeks_do_not_change_earlier_click(self):
        records = [_context(_t(-5)), _greeks(_t(1), skew=15.0)]
        evidence = deribit_options_evidence_from_pit_records(
            records,
            decision_at=_t(),
            policy=DeribitOptionsEvidencePolicy(min_prior_iv_samples=2, max_snapshot_age_seconds=60, max_greeks_age_seconds=10),
        )
        self.assertEqual(evidence.metadata["chain_context_status"], "READY")
        self.assertEqual(evidence.metadata["greeks_status"], "MISSING")
        self.assertIsNone(evidence.metadata["skew_25d"])
        self.assertFalse(evidence.metadata["future_rows_used"])

    def test_strike_inferred_greeks_payload_is_rejected(self):
        bad = _greeks(_t(-2))
        bad["payload"]["skew_25d_inferred_from_strike"] = True
        with self.assertRaises(ValueError):
            deribit_options_evidence_from_pit_records(
                [_context(_t(-5)), bad],
                decision_at=_t(),
                policy=DeribitOptionsEvidencePolicy(min_prior_iv_samples=2, max_snapshot_age_seconds=60, max_greeks_age_seconds=10),
            )

    def test_fresh_greeks_without_chain_does_not_become_coindcx_execution_data(self):
        evidence = deribit_options_evidence_from_pit_records(
            [_greeks(_t(-2), skew=-4.0)],
            decision_at=_t(),
            policy=DeribitOptionsEvidencePolicy(min_prior_iv_samples=2, max_snapshot_age_seconds=60, max_greeks_age_seconds=10),
        )
        self.assertEqual(evidence.metadata["chain_context_status"], "MISSING")
        self.assertEqual(evidence.metadata["greeks_status"], "READY")
        self.assertEqual(evidence.metadata["skew_25d"], -4.0)
        self.assertIsNone(evidence.metadata["raw_atm_mark_iv_pct"])
        self.assertFalse(evidence.metadata["coindcx_contract_selection_allowed"])
        self.assertFalse(evidence.metadata["coindcx_quote_fill_allowed"])
        self.assertFalse(evidence.metadata["coindcx_pnl_replay_allowed"])
        self.assertTrue(evidence.context_only)


if __name__ == "__main__":
    unittest.main()
