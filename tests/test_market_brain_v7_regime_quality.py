import unittest

from app.market_brain_v7_regime_quality import (
    FEATURE_NAMES,
    _feature_vector,
    evaluate_market_brain_v7,
)


def _observation(observation_id, timestamp, score, win, role, block_id):
    features = {
        "breadth_alignment": score,
        "flow_alignment": score * 0.8,
        "nifty_vwap_alignment": score * 0.6,
        "bank_vwap_alignment": score * 0.5,
        "nifty_trend_alignment": score * 0.7,
        "bank_trend_alignment": score * 0.4,
        "volatility_expansion": 1.0 + abs(score) * 0.05,
    }
    return {
        "observation_id": observation_id,
        "block_id": block_id,
        "role": role,
        "symbol": "RELIANCE",
        "timestamp": timestamp,
        "direction": "LONG",
        "r_multiple": 1.0 if win else -1.0,
        "win": 1 if win else 0,
        "features": features,
    }


class MarketBrainV7Tests(unittest.TestCase):
    def test_feature_vector_signs_directional_features_only(self):
        context = {
            "breadth_score": 50.0,
            "flow_score": 25.0,
            "nifty": {
                "vwap_distance_pct": 0.50,
                "trend_return_pct": 0.75,
                "atr_pct": 0.25,
                "volatility_expansion": 1.5,
            },
            "bank": {
                "vwap_distance_pct": 0.25,
                "trend_return_pct": 0.50,
                "atr_pct": 0.25,
                "volatility_expansion": 2.5,
            },
        }

        long_features = _feature_vector(context, "LONG")
        short_features = _feature_vector(context, "SHORT")

        for name in FEATURE_NAMES[:-1]:
            self.assertEqual(short_features[name], -long_features[name])
        self.assertEqual(long_features["volatility_expansion"], 2.0)
        self.assertEqual(short_features["volatility_expansion"], 2.0)

    def test_evaluator_is_deterministic_and_can_recognize_ordering(self):
        development = []
        for index in range(60):
            day = 25 + index // 12
            slot = index % 12
            hour = 9 + (45 + slot * 15) // 60
            minute = (45 + slot * 15) % 60
            development.append(_observation(
                f"D-{index}",
                f"2026-05-{day:02d}T{hour:02d}:{minute:02d}:00+05:30",
                (index - 29.5) / 10.0,
                index >= 30,
                "DEVELOPMENT",
                "S-0A",
            ))

        holdout = []
        for index in range(36):
            day = 11 + index // 12
            slot = index % 12
            hour = 9 + (45 + slot * 15) // 60
            minute = (45 + slot * 15) % 60
            holdout.append(_observation(
                f"H-{index}",
                f"2026-08-{day:02d}T{hour:02d}:{minute:02d}:00+05:30",
                (index - 17.5) / 6.0,
                index >= 18,
                "HOLDOUT",
                "H-1",
            ))

        first = evaluate_market_brain_v7(development, holdout)
        second = evaluate_market_brain_v7(development, holdout)

        self.assertEqual(first["decision"], "VALIDATED_CONTINUOUS_REGIME_QUALITY_CANDIDATE")
        self.assertEqual(first["model"], second["model"])
        self.assertEqual(first["probability_metrics"], second["probability_metrics"])
        self.assertEqual(first["probability_bands"], second["probability_bands"])
        self.assertEqual(first["probability_metrics"]["roc_auc"], 1.0)
        self.assertTrue(all(first["acceptance_gates"].values()))

    def test_evaluator_rejects_holdout_contamination(self):
        development = [
            _observation(
                "D-1",
                "2026-05-25T10:00:00+05:30",
                -1.0,
                False,
                "DEVELOPMENT",
                "S-0A",
            ),
            _observation(
                "D-2",
                "2026-05-25T10:15:00+05:30",
                1.0,
                True,
                "DEVELOPMENT",
                "S-0A",
            ),
        ]
        contaminated = [
            _observation(
                "H-1",
                "2026-08-10T10:00:00+05:30",
                1.0,
                True,
                "HOLDOUT",
                "H-1",
            )
        ]

        with self.assertRaisesRegex(ValueError, "locked H-1 dates"):
            evaluate_market_brain_v7(development, contaminated)


if __name__ == "__main__":
    unittest.main()
