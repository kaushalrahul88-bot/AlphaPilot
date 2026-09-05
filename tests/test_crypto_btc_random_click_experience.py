import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_random_click_experience import (
    BtcClickDecisionRecord,
    BtcExperiencePolicy,
    BtcForwardPriceObservation,
    BtcRandomClickPolicy,
    analyze_no_trade_follow_through,
    architecture_contract,
    build_experience_entry,
    generate_random_clicks,
    summarize_experience_ledger,
)


def _t(hour=4, minute=0, second=0):
    return datetime(2026, 9, 5, hour, minute, second, tzinfo=timezone.utc)


def _decision(final_decision="NO_TRADE", **overrides):
    values = dict(
        click_id="btc-click-1",
        decision_at=_t(),
        decision_btc_price=100_000.0,
        final_decision=final_decision,
        market_direction="UNKNOWN" if final_decision == "NO_TRADE" else "BULLISH" if final_decision == "BUY_CALL" else "BEARISH",
        pipeline_status="NO_UNDERLYING_THESIS" if final_decision == "NO_TRADE" else "OPTIONS_RISK_PLAN_READY",
        reason_codes=("INSUFFICIENT_INDEPENDENT_CONFIRMATION",) if final_decision == "NO_TRADE" else (),
        available_lanes=("BTC_SPOT_STRUCTURE",),
        missing_lanes=("NEWS", "ONCHAIN") if final_decision == "NO_TRADE" else (),
        latest_evidence_at=_t(3, 59, 59),
    )
    values.update(overrides)
    return BtcClickDecisionRecord(**values)


def _policy():
    return BtcExperiencePolicy(no_trade_learning_horizon_hours=4.0, large_move_threshold_pct=3.0)


def _closed_replay(side="BUY_CALL", pnl=500.0, r=1.5):
    return {
        "version": "BTC_OPTIONS_SHADOW_REPLAY_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": "SHADOW_TRADE_CLOSED",
        "side_candidate": side,
        "contract_symbol": "BTC-TEST",
        "decision_at": _t().isoformat(),
        "exit_at": _t(5).isoformat(),
        "exit_reason": "UNDERLYING_TARGET" if pnl > 0 else "UNDERLYING_INVALIDATION",
        "net_pnl_account": pnl,
        "net_return_pct_on_premium_outlay": 10.0,
        "realized_r_vs_planned_stop": r,
        "actual_quote_used_for_pnl": True,
        "model_reference_used_as_fill": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
    }


class BtcRandomClickExperienceTests(unittest.TestCase):
    def test_random_clicks_are_reproducible_from_seed(self):
        policy = BtcRandomClickPolicy(_t(), _t(8), click_count=10, seed=42, min_spacing_seconds=60)
        first = generate_random_clicks(policy)
        second = generate_random_clicks(policy)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 10)
        self.assertEqual(len(set(first)), 10)

    def test_different_seed_changes_clicks(self):
        a = generate_random_clicks(BtcRandomClickPolicy(_t(), _t(8), 10, 42))
        b = generate_random_clicks(BtcRandomClickPolicy(_t(), _t(8), 10, 43))
        self.assertNotEqual(a, b)

    def test_random_clicks_stay_inside_window_and_respect_spacing(self):
        start, end = _t(), _t(1)
        rows = generate_random_clicks(BtcRandomClickPolicy(start, end, 12, 7, min_spacing_seconds=120))
        self.assertTrue(all(start <= row < end for row in rows))
        self.assertTrue(all((b - a).total_seconds() >= 120 for a, b in zip(rows, rows[1:])))

    def test_impossible_dense_click_policy_fails(self):
        with self.assertRaises(ValueError):
            generate_random_clicks(BtcRandomClickPolicy(_t(), _t(0, 1), 10, 1, min_spacing_seconds=30))

    def test_future_evidence_at_click_is_rejected(self):
        with self.assertRaises(ValueError):
            _decision(latest_evidence_at=_t(4, 0, 1)).validated()

    def test_no_trade_large_up_move_is_flagged_for_postmortem(self):
        rows = [
            BtcForwardPriceObservation(_t(5), 101_000),
            BtcForwardPriceObservation(_t(6), 104_500),
        ]
        result = analyze_no_trade_follow_through(
            decision=_decision(), forward_prices=rows, experience_policy=_policy()
        )
        self.assertTrue(result["large_move_missed"])
        self.assertEqual(result["classification"], "MISSED_LARGE_MOVE_UP")
        self.assertEqual(result["missed_direction"], "UP")
        self.assertTrue(result["requires_postmortem_if_large_move_missed"])
        self.assertFalse(result["decision_rewritten"])

    def test_no_trade_large_down_move_is_flagged(self):
        rows = [BtcForwardPriceObservation(_t(5), 98_000), BtcForwardPriceObservation(_t(6), 95_000)]
        result = analyze_no_trade_follow_through(
            decision=_decision(), forward_prices=rows, experience_policy=_policy()
        )
        self.assertEqual(result["classification"], "MISSED_LARGE_MOVE_DOWN")
        self.assertEqual(result["missed_direction"], "DOWN")

    def test_no_trade_two_sided_whipsaw_is_identified_separately(self):
        rows = [BtcForwardPriceObservation(_t(5), 104_000), BtcForwardPriceObservation(_t(6), 96_000)]
        result = analyze_no_trade_follow_through(
            decision=_decision(), forward_prices=rows, experience_policy=_policy()
        )
        self.assertEqual(result["classification"], "MISSED_LARGE_MOVE_BOTH_DIRECTIONS")
        self.assertEqual(result["missed_direction"], "BOTH")

    def test_quiet_no_trade_is_not_marked_missed(self):
        rows = [BtcForwardPriceObservation(_t(5), 101_000), BtcForwardPriceObservation(_t(6), 99_500)]
        result = analyze_no_trade_follow_through(
            decision=_decision(), forward_prices=rows, experience_policy=_policy()
        )
        self.assertFalse(result["large_move_missed"])
        self.assertEqual(result["classification"], "NO_LARGE_MOVE_AFTER_NO_TRADE")

    def test_no_trade_without_future_data_is_unresolved_not_imputed(self):
        result = analyze_no_trade_follow_through(
            decision=_decision(), forward_prices=[], experience_policy=_policy()
        )
        self.assertEqual(result["status"], "NO_TRADE_FOLLOW_THROUGH_UNRESOLVED")
        self.assertIsNone(result["large_move_missed"])

    def test_no_trade_cannot_contain_trade_replay(self):
        with self.assertRaises(ValueError):
            build_experience_entry(
                decision=_decision(),
                replay_result=_closed_replay(),
                forward_prices=[],
                experience_policy=_policy(),
            )

    def test_closed_trade_uses_shadow_replay_outcome(self):
        row = build_experience_entry(
            decision=_decision("BUY_CALL"),
            replay_result=_closed_replay(),
            forward_prices=None,
            experience_policy=_policy(),
        )
        self.assertEqual(row["outcome_type"], "TRADE_CLOSED")
        self.assertTrue(row["performance_eligible"])
        self.assertTrue(row["trade_outcome"]["actual_quote_used_for_pnl"])
        self.assertFalse(row["trade_outcome"]["model_reference_used_as_fill"])
        self.assertTrue(row["trade_outcome"]["win"])

    def test_replay_decision_time_must_match_click(self):
        replay = _closed_replay()
        replay["decision_at"] = _t(4, 1).isoformat()
        with self.assertRaises(ValueError):
            build_experience_entry(
                decision=_decision("BUY_CALL"),
                replay_result=replay,
                forward_prices=None,
                experience_policy=_policy(),
            )

    def test_replay_side_must_match_frozen_decision(self):
        with self.assertRaises(ValueError):
            build_experience_entry(
                decision=_decision("BUY_PUT"),
                replay_result=_closed_replay(side="BUY_CALL"),
                forward_prices=None,
                experience_policy=_policy(),
            )

    def test_unresolved_trade_is_excluded_from_performance(self):
        row = build_experience_entry(
            decision=_decision("BUY_CALL"),
            replay_result={
                "instrument_type": "OPTIONS",
                "status": "UNRESOLVED_EXIT_QUOTE_GAP",
                "side_candidate": "BUY_CALL",
                "decision_at": _t().isoformat(),
                "reason": "missing quote",
                "futures_route_invoked": False,
                "futures_trade_generated": False,
            },
            forward_prices=None,
            experience_policy=_policy(),
        )
        self.assertEqual(row["outcome_type"], "TRADE_UNRESOLVED")
        self.assertFalse(row["performance_eligible"])

    def test_futures_state_is_rejected(self):
        with self.assertRaises(ValueError):
            _decision(futures_route_invoked=True).validated()

    def test_summary_keeps_unresolved_and_no_trade_out_of_trade_performance(self):
        closed_win = build_experience_entry(
            decision=_decision("BUY_CALL", click_id="a"),
            replay_result=_closed_replay(pnl=500.0, r=1.5),
            forward_prices=None,
            experience_policy=_policy(),
        )
        closed_loss = build_experience_entry(
            decision=_decision("BUY_CALL", click_id="b"),
            replay_result=_closed_replay(pnl=-200.0, r=-0.6),
            forward_prices=None,
            experience_policy=_policy(),
        )
        no_trade = build_experience_entry(
            decision=_decision(click_id="c"),
            replay_result=None,
            forward_prices=[BtcForwardPriceObservation(_t(5), 104_000)],
            experience_policy=_policy(),
        )
        unresolved = build_experience_entry(
            decision=_decision("BUY_PUT", click_id="d"),
            replay_result=None,
            forward_prices=None,
            experience_policy=_policy(),
        )
        summary = summarize_experience_ledger([closed_win, closed_loss, no_trade, unresolved])
        self.assertEqual(summary["click_count"], 4)
        self.assertEqual(summary["closed_trade_count"], 2)
        self.assertEqual(summary["unresolved_trade_count"], 1)
        self.assertEqual(summary["no_trade_count"], 1)
        self.assertEqual(summary["net_pnl_account"], 300.0)
        self.assertEqual(summary["win_rate_pct"], 50.0)
        self.assertEqual(summary["no_trade_large_move_missed_count"], 1)
        self.assertTrue(summary["unresolved_excluded_from_performance"])
        self.assertFalse(summary["future_outcomes_used_to_select_clicks"])

    def test_duplicate_click_ids_are_rejected(self):
        row = build_experience_entry(
            decision=_decision(click_id="same"),
            replay_result=None,
            forward_prices=[],
            experience_policy=_policy(),
        )
        with self.assertRaises(ValueError):
            summarize_experience_ledger([row, row])

    def test_architecture_contract_is_outcome_blind_and_options_only(self):
        contract = architecture_contract()
        self.assertTrue(contract["crypto_market_is_24_7"])
        self.assertFalse(contract["click_selection_uses_future_outcomes"])
        self.assertTrue(contract["decision_evidence_must_not_be_after_click"])
        self.assertTrue(contract["no_trade_large_move_requires_postmortem"])
        self.assertFalse(contract["future_outcomes_may_rewrite_historical_decision"])
        self.assertFalse(contract["futures_route_invoked"])
        self.assertFalse(contract["broker_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
