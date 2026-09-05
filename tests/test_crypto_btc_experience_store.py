import copy
import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_experience_store import (
    ImmutableBtcExperienceLedger,
    ResolvedBtcExperienceRecord,
    architecture_contract,
    resolved_experience_record_from_entry,
)


def _t(hours=0, minutes=0):
    return datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc) + timedelta(hours=hours, minutes=minutes)


def _closed_entry(click_id="c1"):
    return {
        "version": "BTC_RANDOM_CLICK_EXPERIENCE_V1",
        "click_id": click_id,
        "asset": "BTC",
        "instrument_type": "OPTIONS",
        "decision_at": _t().isoformat(),
        "final_decision": "BUY_CALL",
        "market_direction": "BULLISH",
        "pipeline_status": "SHADOW_TRADE",
        "decision_frozen_before_outcome": True,
        "future_outcome_may_rewrite_decision": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed_live": 0,
        "outcome_type": "TRADE_CLOSED",
        "trade_outcome": {
            "status": "SHADOW_TRADE_CLOSED",
            "contract_symbol": "BTC-TEST-C",
            "exit_reason": "UNDERLYING_TARGET",
            "exit_at": _t(minutes=30).isoformat(),
            "net_pnl_account": 100.0,
            "realized_r_vs_planned_stop": 1.2,
            "actual_quote_used_for_pnl": True,
            "model_reference_used_as_fill": False,
            "win": True,
        },
        "no_trade_follow_through": None,
        "performance_eligible": True,
        "postmortem_required": False,
    }


def _no_trade_entry(click_id="n1", horizon_hours=2.0):
    return {
        "version": "BTC_RANDOM_CLICK_EXPERIENCE_V1",
        "click_id": click_id,
        "asset": "BTC",
        "instrument_type": "OPTIONS",
        "decision_at": _t().isoformat(),
        "final_decision": "NO_TRADE",
        "market_direction": "UNKNOWN",
        "pipeline_status": "NO_TRADE",
        "decision_frozen_before_outcome": True,
        "future_outcome_may_rewrite_decision": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed_live": 0,
        "outcome_type": "NO_TRADE_LEARNING",
        "trade_outcome": None,
        "no_trade_follow_through": {
            "status": "NO_TRADE_FOLLOW_THROUGH_RESOLVED",
            "classification": "MISSED_LARGE_MOVE_UP",
            "large_move_missed": True,
            "learning_horizon_hours": horizon_hours,
            "max_up_pct": 3.0,
            "max_down_pct": -0.5,
            "decision_rewritten": False,
            "outcome_used_for_original_decision": False,
        },
        "performance_eligible": False,
        "postmortem_required": True,
    }


class CryptoBtcExperienceStoreTests(unittest.TestCase):
    def test_closed_trade_memory_requires_resolution_at_or_after_actual_exit(self):
        entry = _closed_entry()
        with self.assertRaises(ValueError):
            resolved_experience_record_from_entry(entry=entry, resolved_at=_t(minutes=29))
        record = resolved_experience_record_from_entry(entry=entry, resolved_at=_t(minutes=30))
        self.assertEqual(record.outcome_type, "TRADE_CLOSED")

    def test_closed_trade_requires_actual_archived_quote_and_not_model_fill(self):
        entry = _closed_entry()
        entry["trade_outcome"]["actual_quote_used_for_pnl"] = False
        with self.assertRaises(ValueError):
            resolved_experience_record_from_entry(entry=entry, resolved_at=_t(minutes=30))
        entry = _closed_entry()
        entry["trade_outcome"]["model_reference_used_as_fill"] = True
        with self.assertRaises(ValueError):
            resolved_experience_record_from_entry(entry=entry, resolved_at=_t(minutes=30))

    def test_no_trade_memory_requires_full_learning_horizon(self):
        entry = _no_trade_entry(horizon_hours=2.0)
        with self.assertRaises(ValueError):
            resolved_experience_record_from_entry(entry=entry, resolved_at=_t(hours=1, minutes=59))
        record = resolved_experience_record_from_entry(entry=entry, resolved_at=_t(hours=2))
        self.assertEqual(record.outcome_type, "NO_TRADE_LEARNING")

    def test_unresolved_case_cannot_enter_memory(self):
        entry = _closed_entry()
        entry["outcome_type"] = "TRADE_UNRESOLVED"
        entry["performance_eligible"] = False
        with self.assertRaises(ValueError):
            resolved_experience_record_from_entry(entry=entry, resolved_at=_t(hours=1))

    def test_futures_state_is_hard_rejected(self):
        entry = _closed_entry()
        entry["futures_route_invoked"] = True
        with self.assertRaises(ValueError):
            resolved_experience_record_from_entry(entry=entry, resolved_at=_t(minutes=30))
        with self.assertRaises(ValueError):
            ResolvedBtcExperienceRecord(
                click_id="bad",
                decision_at=_t(),
                resolved_at=_t(hours=1),
                outcome_type="TRADE_CLOSED",
                payload=_closed_entry("bad"),
                instrument_type="FUTURES",
            ).validated()

    def test_first_resolved_record_wins_and_exact_duplicate_is_idempotent(self):
        record = resolved_experience_record_from_entry(entry=_closed_entry(), resolved_at=_t(minutes=30))
        ledger = ImmutableBtcExperienceLedger()
        first = ledger.insert_resolved(record)
        duplicate = ledger.insert_resolved(record)
        self.assertEqual(first["status"], "INSERTED_RESOLVED_EXPERIENCE")
        self.assertEqual(duplicate["status"], "IDEMPOTENT_RESOLVED_EXPERIENCE")
        self.assertEqual(first["record"]["record_fingerprint"], duplicate["record"]["record_fingerprint"])

    def test_conflicting_later_outcome_cannot_rewrite_same_click_memory(self):
        first_entry = _closed_entry()
        first = resolved_experience_record_from_entry(entry=first_entry, resolved_at=_t(minutes=30))
        changed_entry = copy.deepcopy(first_entry)
        changed_entry["trade_outcome"]["net_pnl_account"] = -50.0
        changed_entry["trade_outcome"]["win"] = False
        changed = resolved_experience_record_from_entry(entry=changed_entry, resolved_at=_t(minutes=30))
        ledger = ImmutableBtcExperienceLedger()
        ledger.insert_resolved(first)
        with self.assertRaises(ValueError):
            ledger.insert_resolved(changed)

    def test_memory_is_visible_only_strictly_after_resolution(self):
        record = resolved_experience_record_from_entry(entry=_closed_entry(), resolved_at=_t(minutes=30))
        ledger = ImmutableBtcExperienceLedger()
        ledger.insert_resolved(record)
        self.assertEqual(ledger.visible_strictly_before(_t(minutes=30)), [])
        self.assertEqual(len(ledger.visible_strictly_before(_t(minutes=31))), 1)

    def test_old_decision_must_remain_frozen(self):
        entry = _closed_entry()
        entry["future_outcome_may_rewrite_decision"] = True
        with self.assertRaises(ValueError):
            resolved_experience_record_from_entry(entry=entry, resolved_at=_t(minutes=30))

    def test_contract_keeps_outcomes_out_of_market_data_pit_archive(self):
        contract = architecture_contract()
        self.assertFalse(contract["market_data_pit_archive_used_for_outcomes"])
        self.assertTrue(contract["dedicated_experience_memory"])
        self.assertFalse(contract["unresolved_case_admitted"])
        self.assertFalse(contract["case_visible_at_exact_resolution_timestamp"])
        self.assertTrue(contract["only_strictly_prior_resolved_cases_visible"])
        self.assertTrue(contract["no_trade_full_horizon_required"])
        self.assertFalse(contract["futures_state_allowed"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
