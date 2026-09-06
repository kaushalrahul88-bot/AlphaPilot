from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.fno_prospective_capture_v1 import deterministic_batch
from app.fno_prospective_protocol_v1 import (
    PRIMARY_HORIZON_MINUTES,
    architecture_contract as protocol_contract,
    session_outcome_eligible,
)
from app.fno_prospective_resolver_v1 import build_outcome
from app.fno_prospective_store_v1 import (
    EPISODE_TABLE,
    IMMUTABILITY_SQL,
    OBSERVATION_TABLE,
    OUTCOME_TABLE,
    episode_params,
)
from app.fno_selected_contract_tape_v1 import build_selected_observation

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")


def _perception(symbol="RELIANCE"):
    return {
        "perception_fingerprint": "p-1",
        "source": {"expiry_date": "2026-09-29"},
        "underlying": {"symbol": symbol, "ltp": 100.0},
        "technical": {"status": "SETUP", "direction": "LONG"},
    }


def _decision(action="BUY_CE", execution_action="NO_TRADE"):
    candidate = {
        "option_type": "CE",
        "trading_symbol": "RELIANCE26SEP100CE",
        "strike": 100.0,
        "ltp": 50.0,
        "open_interest": 10,
    }
    return {
        "research_action": action,
        "research_candidate": candidate if action != "NO_TRADE" else None,
        "execution_action": execution_action,
        "execution_eligible": False,
        "capital_committed": 0,
        "live_orders_created": False,
    }


def _frozen(action="BUY_CE"):
    now = datetime(2026, 9, 7, 4, 30, tzinfo=UTC)
    return {
        "protocol_id": "FNO_PROSPECTIVE_LEARNING_V1_2026-09-06",
        "episode_id": "fnoep-test",
        "capture_slot_at": now.isoformat(),
        "captured_at": now.isoformat(),
        "decision_at": now.isoformat(),
        "outcome_due_at": (now + timedelta(minutes=60)).isoformat(),
        "outcome_eligible": True,
        "perception": _perception(),
        "memory": {},
        "decision": _decision(action),
        "future_outcome_present_in_decision": False,
        "outcome_used_for_decision": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
    }


class FnoProspectiveLearningV1Tests(unittest.TestCase):
    def test_protocol_is_fixed_research_only(self):
        contract = protocol_contract()
        self.assertEqual(PRIMARY_HORIZON_MINUTES, 60)
        self.assertEqual(contract["capture_cadence_minutes"], 15)
        self.assertEqual(contract["selected_contract_cadence_minutes"], 5)
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)
        self.assertFalse(contract["promotion_eligible"])
        self.assertFalse(contract["future_outcome_allowed_in_decision"])

    def test_fixed_horizon_must_fit_session(self):
        eligible = datetime(2026, 9, 7, 10, 30, tzinfo=IST)
        late = datetime(2026, 9, 7, 14, 50, tzinfo=IST)
        self.assertTrue(session_outcome_eligible(eligible))
        self.assertFalse(session_outcome_eligible(late))

    def test_database_immutability_blocks_row_mutation_and_truncate(self):
        sql = " ".join(IMMUTABILITY_SQL.upper().split())
        for table in (EPISODE_TABLE, OBSERVATION_TABLE, OUTCOME_TABLE):
            self.assertIn(f"BEFORE UPDATE OR DELETE ON {table.upper()}", sql)
            self.assertIn(f"BEFORE TRUNCATE ON {table.upper()}", sql)
        self.assertIn("RAISE EXCEPTION", sql)

    def test_episode_persistence_rejects_execution_or_future_outcome(self):
        record = _frozen()
        params = episode_params(record)
        self.assertEqual(params["execution_action"], "NO_TRADE")
        self.assertFalse(params["execution_eligible"])
        self.assertEqual(params["capital_committed"], 0)

        bad = _frozen()
        bad["decision"]["execution_action"] = "BUY_CE"
        with self.assertRaises(ValueError):
            episode_params(bad)

        bad = _frozen()
        bad["future_outcome_present_in_decision"] = True
        with self.assertRaises(ValueError):
            episode_params(bad)

    def test_selected_observation_keeps_missing_bid_ask_missing(self):
        episode = {
            "episode_id": "fnoep-test",
            "underlying_symbol": "RELIANCE",
            "expiry_date": "2026-09-29",
            "trading_symbol": "RELIANCE26SEP100CE",
            "strike": 100.0,
            "option_type": "CE",
        }
        chain = {
            "data": {
                "payload": {
                    "underlying_ltp": 100.0,
                    "strikes": {
                        "100": {
                            "CE": {
                                "trading_symbol": "RELIANCE26SEP100CE",
                                "ltp": 50.0,
                                "open_interest": 10,
                                "volume": 5,
                                "greeks": {"iv": 20, "delta": 0.5},
                            },
                            "PE": {},
                        }
                    },
                }
            }
        }
        record = build_selected_observation(
            episode,
            chain,
            collected_at=datetime(2026, 9, 7, 4, 30, tzinfo=UTC),
            direct_quote={"status": "UNAVAILABLE"},
        )
        self.assertEqual(record["ltp"], 50.0)
        self.assertIsNone(record["best_bid"])
        self.assertIsNone(record["best_ask"])
        self.assertFalse(record["bid_ask_available"])
        self.assertTrue(record["payload"]["provider_bid_ask_not_fabricated"])

    def test_no_trade_can_resolve_from_underlying_only(self):
        frozen = _frozen("NO_TRADE")
        decision_at = datetime.fromisoformat(frozen["decision_at"])
        episode = {
            "episode_id": frozen["episode_id"],
            "decision_at": decision_at,
            "outcome_due_at": decision_at + timedelta(minutes=60),
            "outcome_eligible": True,
            "research_action": "NO_TRADE",
            "selected_reference_ltp": None,
            "payload": frozen,
        }
        candles = [
            [(decision_at + timedelta(minutes=5)).isoformat(), 100, 101, 99, 100.5],
            [(decision_at + timedelta(minutes=60)).isoformat(), 100.5, 103, 100, 102],
        ]
        outcome = build_outcome(
            episode, candles, [], resolved_at=decision_at + timedelta(minutes=61)
        )
        self.assertEqual(outcome["resolution_status"], "RESOLVED")
        self.assertEqual(outcome["classification"], "NO_TRADE_OBSERVED")
        self.assertTrue(outcome["memory_admission_eligible"])

    def test_actionable_requires_later_exact_option_observation(self):
        frozen = _frozen("BUY_CE")
        decision_at = datetime.fromisoformat(frozen["decision_at"])
        episode = {
            "episode_id": frozen["episode_id"],
            "decision_at": decision_at,
            "outcome_due_at": decision_at + timedelta(minutes=60),
            "outcome_eligible": True,
            "research_action": "BUY_CE",
            "selected_reference_ltp": 50.0,
            "payload": frozen,
        }
        candles = [
            [(decision_at + timedelta(minutes=5)).isoformat(), 100, 101, 99, 100.5],
            [(decision_at + timedelta(minutes=60)).isoformat(), 100.5, 103, 100, 102],
        ]
        one = [{"observed_at": decision_at, "ltp": 50.0}]
        incomplete = build_outcome(
            episode, candles, one, resolved_at=decision_at + timedelta(minutes=61)
        )
        self.assertEqual(incomplete["resolution_status"], "SELECTED_OPTION_TAPE_INCOMPLETE")
        self.assertFalse(incomplete["memory_admission_eligible"])

        two = [
            {"observed_at": decision_at, "ltp": 50.0},
            {"observed_at": decision_at + timedelta(minutes=55), "ltp": 60.0},
        ]
        resolved = build_outcome(
            episode, candles, two, resolved_at=decision_at + timedelta(minutes=61)
        )
        self.assertEqual(resolved["resolution_status"], "RESOLVED")
        self.assertEqual(resolved["classification"], "OPTION_GAIN")
        self.assertEqual(resolved["option_return_pct"], 20.0)
        self.assertTrue(resolved["memory_admission_eligible"])

    def test_deterministic_batch_is_bounded_and_repeatable(self):
        at = datetime(2026, 9, 7, 5, 0, tzinfo=UTC)
        universe = ["A", "B", "C", "D", "E"]
        first = deterministic_batch(at, batch_size=3, universe=universe)
        second = deterministic_batch(at, batch_size=3, universe=reversed(universe))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)
        self.assertEqual(len(set(first)), 3)


if __name__ == "__main__":
    unittest.main()
