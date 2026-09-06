import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_prospective_resolution_runtime import _resolution_for_persistence
from app.crypto_btc_prospective_thesis_postgres import postgres_thesis_resolution_params
from app.crypto_btc_prospective_thesis_tape import (
    ProspectiveBtcThesisTapePolicy,
    freeze_prospective_btc_thesis,
    resolve_prospective_btc_thesis,
    verify_prospective_btc_thesis_resolution,
)
from app.crypto_btc_random_click_experience import BtcForwardPriceObservation
from app.crypto_market_intelligence import Evidence

UTC = timezone.utc
DECISION_AT = datetime(2026, 9, 6, 3, 44, 0, tzinfo=UTC)


class ProspectiveBtcResolutionFingerprintRegressionTests(unittest.TestCase):
    def test_bridge_diagnostics_do_not_invalidate_persisted_resolution(self):
        evidence_at = DECISION_AT - timedelta(minutes=1)
        evidence = [
            Evidence(
                family="BTC_SPOT_STRUCTURE",
                causal_origin="SPOT_PRICE_STRUCTURE",
                stance="BULLISH",
                strength="MEDIUM",
                confidence=0.75,
                observed_at=evidence_at,
                reason="Regression fixture spot structure.",
                context_only=False,
                source="TEST_SPOT",
                metadata={"fixture": True},
            ),
            Evidence(
                family="DERIVATIVES_POSITIONING",
                causal_origin="LEVERAGED_POSITIONING",
                stance="BULLISH",
                strength="MEDIUM",
                confidence=0.75,
                observed_at=evidence_at,
                reason="Regression fixture derivatives evidence.",
                context_only=False,
                source="TEST_DERIVATIVES",
                metadata={"fixture": True},
            ),
        ]
        policy = ProspectiveBtcThesisTapePolicy(
            trade_horizon="intraday",
            evaluation_horizon_hours=4.0,
            terminal_price_max_gap_seconds=60,
            neutral_band_pct=0.25,
            large_move_threshold_pct=1.5,
        ).validated()
        frozen = freeze_prospective_btc_thesis(
            click_id="fingerprint-regression",
            decision_at=DECISION_AT,
            btc_spot_price=80_000.0,
            evidence=evidence,
            policy=policy,
        )
        due_at = DECISION_AT + timedelta(hours=4)
        core = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=due_at,
            forward_prices=[
                BtcForwardPriceObservation(
                    observed_at=due_at,
                    btc_price=81_600.0,
                )
            ],
        )
        self.assertEqual(core["status"], "THESIS_OUTCOME_RESOLVED")
        self.assertTrue(verify_prospective_btc_thesis_resolution(core))

        bridge_result = {
            **core,
            "provider_called": True,
            "completed_btc_candle_count": 240,
            "outcome_source": "COINDCX_PUBLIC_COMPLETED_SPOT_CANDLES",
            "trade_generated": False,
        }
        self.assertFalse(verify_prospective_btc_thesis_resolution(bridge_result))

        canonical = _resolution_for_persistence(bridge_result)
        self.assertTrue(verify_prospective_btc_thesis_resolution(canonical))
        self.assertNotIn("provider_called", canonical)
        self.assertNotIn("completed_btc_candle_count", canonical)
        self.assertNotIn("outcome_source", canonical)
        self.assertNotIn("trade_generated", canonical)

        params = postgres_thesis_resolution_params(canonical)
        self.assertEqual(params["click_id"], "fingerprint-regression")
        self.assertEqual(params["resolution_fingerprint"], core["resolution_fingerprint"])


if __name__ == "__main__":
    unittest.main()
