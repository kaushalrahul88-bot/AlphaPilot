from __future__ import annotations

import unittest
from unittest.mock import patch

from app import fno_market_brain_v4 as brain


def _payload(side: str = "NEUTRAL", *, expansion: bool = True):
    side = side.upper()
    if side == "LONG":
        alpha = 58.0
        price, ema20, ema50, vwap = 102.0, 101.0, 100.0, 101.0
        macd_hist = 0.35
        structure = "UPTREND_HIGHER_HIGH"
        signal = "LONG"
    elif side == "SHORT":
        alpha = 42.0
        price, ema20, ema50, vwap = 98.0, 99.0, 100.0, 99.0
        macd_hist = -0.35
        structure = "DOWNTREND_LOWER_LOW"
        signal = "SHORT"
    else:
        alpha = 50.0
        price = ema20 = ema50 = vwap = 100.0
        macd_hist = 0.0
        structure = "RANGE"
        signal = "NO_TRADE"

    if expansion:
        volume_ratio = 1.75
        upper, lower = price + 1.5, price - 1.5
        d_res = d_sup = 0.2
    else:
        volume_ratio = 1.0
        upper, lower = price + 3.0, price - 3.0
        d_res = d_sup = 2.0

    return {
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "vwap": vwap,
        "alpha_score": alpha,
        "macd_hist": macd_hist,
        "atr14": 1.0,
        "bollinger_upper": upper,
        "bollinger_lower": lower,
        "volume_ratio_raw": volume_ratio,
        "distance_to_resistance_atr": d_res,
        "distance_to_support_atr": d_sup,
        "market_structure": structure,
        "signal": signal,
    }


def _technical(side: str = "NEUTRAL", *, expansion: bool = True):
    return {
        "timeframes": {
            "5m": _payload(side, expansion=expansion),
            "15m": _payload(side, expansion=expansion),
            "1h": _payload(side, expansion=expansion),
        }
    }


class FnoMarketBrainV4Tests(unittest.TestCase):
    def test_alpha_semantics_are_centered_on_50_and_preserve_42_58_boundaries(self):
        self.assertAlmostEqual(brain._alpha_signal(50), 0.0)
        self.assertAlmostEqual(brain._alpha_signal(58), 1.0)
        self.assertAlmostEqual(brain._alpha_signal(42), -1.0)
        self.assertAlmostEqual(brain._alpha_signal(70), 1.0)
        self.assertAlmostEqual(brain._alpha_signal(30), -1.0)

    def test_bearish_legacy_alpha_is_bearish_not_bullish(self):
        technical = {
            "timeframes": {
                "5m": {"alpha_score": 30.0},
                "15m": {"alpha_score": 30.0},
                "1h": {"alpha_score": 30.0},
            }
        }
        direction = brain.direction_state(technical)
        self.assertEqual(direction["side"], "SHORT")
        self.assertLess(direction["signed_score"], 0)
        alpha_evidence = [
            item for item in direction["evidence"] if item["name"].endswith(":alpha")
        ]
        self.assertTrue(alpha_evidence)
        self.assertTrue(all(item["value"] == -1.0 for item in alpha_evidence))

    def test_neutral_alpha_does_not_create_false_expansion_pressure(self):
        state = brain.expansion_state(_technical("NEUTRAL", expansion=False))
        self.assertFalse(state["ready"])
        self.assertAlmostEqual(state["score"], 0.0)
        pressure = [
            item
            for item in state["evidence"]
            if item["name"].endswith(":technical_pressure")
        ]
        self.assertTrue(pressure)
        self.assertTrue(all(item["value"] == 0.0 for item in pressure))

    def test_strong_bull_and_bear_states_remain_actionable(self):
        long_result = brain.decide("TEST", _technical("LONG", expansion=True))
        short_result = brain.decide("TEST", _technical("SHORT", expansion=True))

        self.assertEqual(long_result["action"], "LONG")
        self.assertEqual(short_result["action"], "SHORT")
        self.assertTrue(long_result["expansion"]["ready"])
        self.assertTrue(short_result["expansion"]["ready"])
        self.assertAlmostEqual(long_result["direction"]["dominance_ratio"], 1.0)
        self.assertAlmostEqual(short_result["direction"]["dominance_ratio"], 1.0)

    def test_dominance_is_scale_free_and_not_duplicate_of_signed_score(self):
        self.assertAlmostEqual(brain._dominance_ratio(6.0, 4.0), 0.20)
        self.assertAlmostEqual(brain._dominance_ratio(3.0, 2.0), 0.20)
        self.assertLess(
            brain._dominance_ratio(5.9, 4.1),
            brain.DIRECTION_DOMINANCE_MIN,
        )

        evidence = [
            brain.Evidence("bull", 1.0, 10.0, 6.0),
            brain.Evidence("bear", -1.0, 10.0, -4.0),
        ]
        aggregate = brain._direction_aggregate(evidence)
        self.assertAlmostEqual(aggregate["signed_score"], 2.0)
        self.assertAlmostEqual(aggregate["dominance"], 0.20)

    def test_decision_rejects_high_magnitude_but_conflicted_direction(self):
        with patch.object(
            brain,
            "expansion_state",
            return_value={"ready": True},
        ), patch.object(
            brain,
            "direction_state",
            return_value={
                "side": "LONG",
                "signed_score": 4.0,
                "dominance_ratio": 0.10,
            },
        ):
            result = brain.decide("TEST", {})

        self.assertEqual(result["action"], "NO_TRADE")
        self.assertIn("DIRECTION_CONFLICT_HIGH", result["reasons"])
        self.assertNotIn("DIRECTION_EVIDENCE_WEAK", result["reasons"])

    def test_point_in_time_event_is_secondary_and_cannot_create_trade_by_itself(self):
        context = {"components": {"events": 2}}
        direction = brain.direction_state({}, context)
        self.assertEqual(direction["side"], "LONG")
        self.assertAlmostEqual(direction["signed_score"], 0.25)
        self.assertLess(direction["signed_score"], brain.DIRECTION_ACTION_SCORE)

    def test_missing_context_is_safe(self):
        result = brain.decide("TEST", _technical("LONG", expansion=True), None)
        self.assertEqual(result["action"], "LONG")

    def test_architecture_contract_is_outcome_blind_and_derivative_free(self):
        contract = brain.architecture_contract()
        self.assertEqual(
            contract["previous_version_frozen"],
            "FNO_MARKET_BRAIN_V3_2026-09-07",
        )
        self.assertEqual(
            contract["alpha_semantics"],
            "CENTER_50_FULL_SCALE_AT_42_58",
        )
        self.assertEqual(
            contract["direction_conflict_metric"],
            "INDEPENDENT_WEIGHTED_DOMINANCE_RATIO",
        )
        self.assertFalse(contract["outcomes_read"])
        self.assertFalse(contract["future_bars_read"])
        self.assertFalse(contract["option_inputs_read"])
        self.assertFalse(contract["futures_inputs_read"])
        self.assertFalse(contract["benchmark_results_used_in_decision"])
        self.assertFalse(contract["thresholds_fit_to_v1_v2_v3_results"])
        self.assertFalse(contract["historical_evaluation_run_by_module"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
