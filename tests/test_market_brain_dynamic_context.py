import unittest

from app.market_brain_setup_expectancy import (
    _build_dynamic_effects,
    _dynamic_context_by_ts,
    _minute_key,
)


def _context(ts, breadth="MIXED", flow="BALANCED", leaders="3-5_LEADERS", nifty="MIXED", bank="MIXED"):
    return {
        "ts":ts,
        "breadth":breadth,
        "flow":flow,
        "leaders":leaders,
        "niftyPhase":nifty,
        "bankPhase":bank,
    }


class DynamicContextTests(unittest.TestCase):
    def test_dynamic_features_use_exact_prior_same_day_observation(self):
        observations = [
            _context("2026-08-03T09:45:00+05:30", "MIXED", "BALANCED", "3-5_LEADERS"),
            _context("2026-08-03T10:00:00+05:30", "BROAD_RISK_ON", "BUYING_PRESSURE", "6+_LEADERS", "ALIGNED_UP", "RECOVERY"),
            _context("2026-08-03T10:30:00+05:30", "BROAD_RISK_OFF", "SELLING_PRESSURE", "0-2_LEADERS", "ALIGNED_DOWN", "FADE"),
            _context("2026-08-04T09:45:00+05:30", "BROAD_RISK_OFF", "SELLING_PRESSURE", "0-2_LEADERS"),
            _context("2026-08-04T10:00:00+05:30", "BROAD_RISK_OFF", "SELLING_PRESSURE", "0-2_LEADERS", "ALIGNED_DOWN", "ALIGNED_DOWN"),
        ]

        result = _dynamic_context_by_ts(observations)

        improving = result["2026-08-03 10:00"]
        self.assertEqual(improving["breadthImpulse"], "IMPROVING")
        self.assertEqual(improving["flowImpulse"], "IMPROVING")
        self.assertEqual(improving["leaderImpulse"], "BROADENING")
        self.assertEqual(improving["indexAlignment"], "BULLISH_ALIGNED")
        self.assertEqual(improving["breadthPersistence"], "MIXED_OR_CHANGING")
        self.assertEqual(improving["flowPersistence"], "BALANCED_OR_CHANGING")
        self.assertNotIn("2026-08-03 10:30", result)
        self.assertNotIn("2026-08-04 09:45", result)

        persistent = result["2026-08-04 10:00"]
        self.assertEqual(persistent["breadthImpulse"], "STABLE")
        self.assertEqual(persistent["flowImpulse"], "STABLE")
        self.assertEqual(persistent["leaderImpulse"], "STABLE")
        self.assertEqual(persistent["indexAlignment"], "BEARISH_ALIGNED")
        self.assertEqual(persistent["breadthPersistence"], "PERSISTENT_RISK_OFF")
        self.assertEqual(persistent["flowPersistence"], "PERSISTENT_SELLING")

    def test_minute_key_converts_utc_to_ist(self):
        self.assertEqual(_minute_key("2026-08-03T04:30:00Z"), "2026-08-03 10:00")

    def test_dynamic_effects_use_same_direction_baseline_and_frozen_gate(self):
        matched = []
        for index in range(12):
            matched.append({
                "direction":"LONG",
                "r_multiple":1.0 if index < 9 else -1.0,
                "dynamic_context":{
                    "breadthImpulse":"IMPROVING",
                    "flowImpulse":"STABLE",
                    "leaderImpulse":"STABLE",
                    "indexAlignment":"BULLISH_ALIGNED",
                    "breadthPersistence":"MIXED_OR_CHANGING",
                    "flowPersistence":"BALANCED_OR_CHANGING",
                },
            })
        for _ in range(12):
            matched.append({
                "direction":"LONG",
                "r_multiple":-1.0,
                "dynamic_context":{
                    "breadthImpulse":"STABLE",
                    "flowImpulse":"STABLE",
                    "leaderImpulse":"STABLE",
                    "indexAlignment":"DIVERGENT_OR_MIXED",
                    "breadthPersistence":"MIXED_OR_CHANGING",
                    "flowPersistence":"BALANCED_OR_CHANGING",
                },
            })

        result = _build_dynamic_effects(matched)
        row = next(effect for effect in result["effects"] if effect["label"] == "LONG · breadthImpulse=IMPROVING")

        self.assertEqual(row["trades"], 12)
        self.assertEqual(row["baseline_trades"], 24)
        self.assertEqual(row["state"], "BOOST")
        self.assertGreaterEqual(row["delta_avg_r"], 0.20)
        self.assertGreaterEqual(row["delta_win_rate_pp"], 8.0)


if __name__ == "__main__":
    unittest.main()
