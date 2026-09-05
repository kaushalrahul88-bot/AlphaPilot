import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_derivatives_capture import BTC_LIQUIDATIONS_DATASET, BTC_OPEN_INTEREST_DATASET
from app.crypto_btc_derivatives_evidence import architecture_contract, derivatives_evidence_from_pit_records


def _t(minute=0):
    return datetime(2026, 9, 5, 6, minute, tzinfo=timezone.utc)


def _rows(*, oi_open=100.0, oi_close=110.0, long_liq=1_000_000.0, short_liq=3_000_000.0, liq_event_offset=0):
    event = _t() - timedelta(hours=4)
    return [
        {
            "dataset": BTC_OPEN_INTEREST_DATASET,
            "first_seen_at": _t().isoformat(),
            "event_at": event.isoformat(),
            "payload": {
                "interval": "4h",
                "open_interest_open_usd": oi_open,
                "open_interest_close_usd": oi_close,
            },
        },
        {
            "dataset": BTC_LIQUIDATIONS_DATASET,
            "first_seen_at": _t().isoformat(),
            "event_at": (event + timedelta(seconds=liq_event_offset)).isoformat(),
            "payload": {
                "interval": "4h",
                "long_liquidation_usd": long_liq,
                "short_liquidation_usd": short_liq,
            },
        },
    ]


class CryptoBtcDerivativesEvidenceTests(unittest.TestCase):
    def test_aligned_oi_and_liquidations_can_form_derivatives_evidence(self):
        evidence = derivatives_evidence_from_pit_records(
            _rows(),
            decision_at=_t(),
            price_change_pct=2.0,
        )
        self.assertEqual(evidence.family, "DERIVATIVES_POSITIONING")
        self.assertEqual(evidence.stance, "BULLISH")
        self.assertEqual(evidence.source, "COINGLASS_V4_PIT")
        self.assertAlmostEqual(evidence.metadata["oi_change_pct"], 10.0)
        self.assertFalse(evidence.metadata["may_generate_futures_trade"])

    def test_short_squeeze_classification_uses_oi_down_and_short_liquidation_dominance(self):
        evidence = derivatives_evidence_from_pit_records(
            _rows(oi_open=100.0, oi_close=90.0, long_liq=1_000_000.0, short_liq=5_000_000.0),
            decision_at=_t(),
            price_change_pct=3.0,
        )
        self.assertEqual(evidence.stance, "BULLISH")
        self.assertTrue(evidence.metadata["short_squeeze"])

    def test_missing_liquidations_is_context_only_unknown(self):
        evidence = derivatives_evidence_from_pit_records(
            _rows()[:1],
            decision_at=_t(),
            price_change_pct=1.0,
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertFalse(evidence.metadata["liquidations_available"])

    def test_mismatched_intervals_cannot_create_direction(self):
        rows = _rows()
        rows[1]["payload"]["interval"] = "1h"
        evidence = derivatives_evidence_from_pit_records(rows, decision_at=_t(), price_change_pct=1.0)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_misaligned_provider_event_times_cannot_create_direction(self):
        evidence = derivatives_evidence_from_pit_records(
            _rows(liq_event_offset=61),
            decision_at=_t(),
            price_change_pct=1.0,
            max_event_misalignment_seconds=60,
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_future_first_seen_row_is_excluded(self):
        rows = _rows()
        rows[1]["first_seen_at"] = (_t() + timedelta(seconds=1)).isoformat()
        evidence = derivatives_evidence_from_pit_records(rows, decision_at=_t(), price_change_pct=1.0)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_zero_oi_baseline_fails_closed(self):
        evidence = derivatives_evidence_from_pit_records(
            _rows(oi_open=0.0, oi_close=10.0),
            decision_at=_t(),
            price_change_pct=1.0,
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_architecture_contract_preserves_trade_separation(self):
        contract = architecture_contract()
        self.assertTrue(contract["requires_open_interest"])
        self.assertTrue(contract["requires_liquidations"])
        self.assertFalse(contract["missing_leg_may_be_directional"])
        self.assertTrue(contract["futures_data_may_inform_options"])
        self.assertFalse(contract["futures_trade_generation_allowed"])
        self.assertFalse(contract["options_trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
