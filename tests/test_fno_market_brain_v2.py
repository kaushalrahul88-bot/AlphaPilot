from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.fno_market_brain_v2 import (
    architecture_contract,
    build_experience_memory,
    build_perception,
    decide_shadow,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 4, 5, 0, tzinfo=UTC)  # 10:30 IST


def snapshot(at=T0 - timedelta(seconds=30)):
    return {
        "provider": "GROWW",
        "underlying_symbol": "NIFTY",
        "expiry_date": "2026-09-10",
        "observed_at": at,
        "payload": {
            "provider": "GROWW",
            "symbol": "NIFTY",
            "expiry": "2026-09-10",
            "data": {
                "status": "SUCCESS",
                "payload": {
                    "underlying_ltp": 25010,
                    "strikes": {
                        "25000": {
                            "CE": {"trading_symbol": "NIFTYCE", "ltp": 120, "open_interest": 1000, "volume": 500, "greeks": {"iv": 15, "delta": 0.52, "gamma": 0.01, "theta": -2, "vega": 5}},
                            "PE": {"trading_symbol": "NIFTYPE", "ltp": 110, "open_interest": 1200, "volume": 600, "greeks": {"iv": 16, "delta": -0.48, "gamma": 0.01, "theta": -2, "vega": 5}},
                        },
                        "25100": {
                            "CE": {"trading_symbol": "NIFTY25100CE", "ltp": 80, "open_interest": 1500, "volume": 400, "greeks": {"iv": 15.5, "delta": 0.42}},
                            "PE": {"trading_symbol": "NIFTY25100PE", "ltp": 150, "open_interest": 900, "volume": 300, "greeks": {"iv": 16.5, "delta": -0.58}},
                        },
                    },
                },
            },
        },
    }


class FnoMarketBrainV2Tests(unittest.TestCase):
    def test_future_snapshot_fails_closed(self):
        with self.assertRaises(ValueError):
            build_perception(snapshot(T0 + timedelta(microseconds=1)), decision_at=T0)

    def test_perception_is_outcome_blind_and_derives_chain_state(self):
        p = build_perception(snapshot(), decision_at=T0, technical={"status": "SETUP", "direction": "LONG"})
        self.assertFalse(p["outcomes_used"])
        self.assertEqual(p["derivatives"]["atm_strike"], 25000.0)
        self.assertGreater(p["derivatives"]["pcr_oi"], 0)
        self.assertTrue(p["quality"]["research_complete"])
        self.assertFalse(p["quality"]["execution_quote_complete"])
        self.assertEqual(p["market_phase"], "CONTINUOUS")

    def test_memory_requires_strictly_prior_case(self):
        current = build_perception(snapshot(), decision_at=T0, technical={"status": "SETUP", "direction": "LONG"})
        prior = build_perception(snapshot(T0 - timedelta(hours=1)), decision_at=T0 - timedelta(minutes=59), technical={"status": "SETUP", "direction": "LONG"})
        future = build_perception(snapshot(T0 + timedelta(hours=1)), decision_at=T0 + timedelta(hours=1, seconds=1), technical={"status": "SETUP", "direction": "LONG"})
        memory = build_experience_memory(current, [prior, future])
        self.assertEqual(memory["prior_perceptions"], 1)
        self.assertTrue(memory["strictly_prior_required"])
        self.assertFalse(memory["outcome_used_for_similarity_ranking"])

    def test_future_outcome_is_not_exposed_to_memory(self):
        current = build_perception(snapshot(), decision_at=T0, technical={"status": "SETUP", "direction": "LONG"})
        prior = build_perception(snapshot(T0 - timedelta(hours=1)), decision_at=T0 - timedelta(minutes=59), technical={"status": "SETUP", "direction": "LONG"})
        memory = build_experience_memory(current, [{"perception": prior, "outcome": {"result": "WIN"}, "outcome_available_at": T0 + timedelta(seconds=1)}])
        self.assertEqual(memory["prior_knowable_outcomes"], 0)
        self.assertIsNone(memory["analogues"][0]["outcome"])

    def test_shadow_decision_never_enables_execution(self):
        p = build_perception(snapshot(), decision_at=T0, technical={"status": "SETUP", "direction": "LONG"})
        d = decide_shadow(p, max_snapshot_age_seconds=60)
        self.assertEqual(d["research_action"], "BUY_CE")
        self.assertEqual(d["execution_action"], "NO_TRADE")
        self.assertFalse(d["execution_eligible"])
        self.assertIn("BID_ASK_EXECUTION_QUOTES_NOT_CAPTURED", d["execution_blockers"])
        self.assertEqual(d["capital_committed"], 0)

    def test_memory_cannot_create_trade_from_no_setup(self):
        p = build_perception(snapshot(), decision_at=T0, technical={"status": "NO_TRADE"})
        d = decide_shadow(p, memory={"status": "ANALOGUES_AVAILABLE"}, max_snapshot_age_seconds=60)
        self.assertEqual(d["research_action"], "NO_TRADE")
        self.assertFalse(d["memory_created_setup"])
        self.assertFalse(d["memory_changed_direction"])

    def test_architecture_contract(self):
        contract = architecture_contract()
        self.assertTrue(contract["shadow_only"])
        self.assertTrue(contract["perception_point_in_time_only"])
        self.assertTrue(contract["memory_strictly_prior"])
        self.assertFalse(contract["outcomes_used_for_similarity_ranking"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)
        self.assertFalse(contract["futures_trade_generation"])


if __name__ == "__main__":
    unittest.main()
