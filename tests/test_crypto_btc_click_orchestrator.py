import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_click_orchestrator import (
    architecture_contract,
    attach_btc_options_click_outcome,
    run_btc_options_click_decision,
    verify_frozen_click_decision,
)
from app.crypto_btc_options_contract_selector import BtcOptionContractSnapshot
from app.crypto_btc_options_exit_geometry import BtcOptionsUnderlyingThesis
from app.crypto_btc_options_risk import BtcOptionsExecutionSpec, BtcOptionsRiskPolicy
from app.crypto_btc_options_shadow_replay import BtcOptionsReplayCostSpec, BtcOptionsReplayObservation
from app.crypto_btc_perception import (
    BtcOptionsMarketSnapshot,
    BtcSpotStructureSnapshot,
    options_market_context,
    spot_structure_context,
)
from app.crypto_btc_random_click_experience import BtcExperiencePolicy, BtcForwardPriceObservation
from app.crypto_market_intelligence import derivatives_context


def _t(hour=4, minute=0, second=0):
    return datetime(2026, 9, 5, hour, minute, second, tzinfo=timezone.utc)


def _evidence(include_options=True):
    rows = [
        spot_structure_context(
            BtcSpotStructureSnapshot(
                observed_at=_t(3, 59),
                price=100_000.0,
                return_1h_pct=0.8,
                return_4h_pct=2.0,
                return_24h_pct=3.5,
                close_location=0.8,
                volume_percentile=0.75,
                breakout_state="UPSIDE_CONFIRMED",
            )
        ),
        derivatives_context(
            observed_at=_t(3, 59),
            price_change_pct=2.0,
            oi_change_pct=6.0,
            funding_percentile=0.55,
            short_liquidations_usd=1_000_000,
            long_liquidations_usd=200_000,
        ),
    ]
    if include_options:
        rows.append(
            options_market_context(
                BtcOptionsMarketSnapshot(
                    observed_at=_t(3, 59, 30),
                    atm_iv_percentile=0.55,
                    put_call_skew_25d=1.0,
                    put_call_oi_ratio=0.9,
                )
            )
        )
    return rows


def _contract(observed_at=None, spread=2.0):
    bid = 100.0 - spread / 2.0
    ask = 100.0 + spread / 2.0
    return BtcOptionContractSnapshot(
        symbol="BTC-TEST-CALL",
        option_type="CALL",
        strike=100_000.0,
        expiry_at=_t() + timedelta(hours=24),
        observed_at=observed_at or _t(3, 59, 45),
        bid=bid,
        ask=ask,
        mark=100.0,
        delta=0.5,
        gamma=1e-8,
        theta=-1.0,
        vega=1.0,
        implied_volatility=60.0,
        open_interest=100.0,
        volume_24h=100.0,
    )


def _thesis(entry=100_000.0):
    return BtcOptionsUnderlyingThesis(
        entry_btc_price=entry,
        invalidation_btc_price=98_000.0,
        target_btc_price=103_000.0,
        expected_holding_hours=4.0,
        stop_time_hours=1.0,
        target_time_hours=3.0,
        stop_iv_change_points=0.0,
        target_iv_change_points=0.0,
        iv_stress_points=0.0,
    )


def _risk_policy():
    return BtcOptionsRiskPolicy(
        account_equity=100_000.0,
        max_premium_allocation_pct_of_equity=20.0,
        max_planned_loss_pct_of_equity=5.0,
        max_tail_loss_pct_of_equity=20.0,
        min_net_reward_risk=0.5,
    )


def _execution_spec():
    return BtcOptionsExecutionSpec(
        account_currency="INR",
        premium_currency="TEST",
        premium_to_account_rate=1.0,
        contract_multiplier=1.0,
        quantity_step=1.0,
        min_quantity=1.0,
        max_quantity=None,
        entry_slippage_pct_of_premium=0.0,
        exit_slippage_pct_of_premium=0.0,
        entry_fee_per_quantity_account=0.0,
        stop_exit_fee_per_quantity_account=0.0,
        target_exit_fee_per_quantity_account=0.0,
    )


def _decision(**overrides):
    values = dict(
        click_id="click-1",
        decision_at=_t(),
        trade_horizon="intraday",
        evidence=_evidence(),
        contracts=[_contract()],
        btc_spot_price=100_000.0,
        expected_move_pct=3.0,
        expected_holding_hours=4.0,
        fee_bps_per_side=1.0,
        underlying_thesis=_thesis(),
        risk_policy=_risk_policy(),
        execution_spec=_execution_spec(),
        iv_percentile=0.55,
    )
    values.update(overrides)
    return run_btc_options_click_decision(**values)


class BtcClickOrchestratorTests(unittest.TestCase):
    def test_full_bullish_click_freezes_buy_call_shadow_plan(self):
        result = _decision()
        self.assertEqual(result["status"], "CLICK_DECISION_FROZEN")
        self.assertEqual(result["decision_record"]["final_decision"], "BUY_CALL")
        self.assertEqual(result["decision_record"]["pipeline_status"], "OPTIONS_SHADOW_PLAN_READY")
        self.assertEqual(result["contract_selection"]["status"], "OPTIONS_CONTRACT_CANDIDATE_SELECTED")
        self.assertEqual(result["exit_geometry"]["status"], "OPTIONS_EXIT_GEOMETRY_READY")
        self.assertEqual(result["risk"]["status"], "OPTIONS_RISK_PLAN_READY")
        self.assertTrue(verify_frozen_click_decision(result))
        self.assertFalse(result["futures_route_invoked"])
        self.assertFalse(result["trade_generated"])

    def test_future_evidence_is_rejected_at_orchestrator_boundary(self):
        rows = _evidence()
        rows.append(
            derivatives_context(
                observed_at=_t(4, 1),
                price_change_pct=5.0,
                oi_change_pct=5.0,
                funding_percentile=0.5,
            )
        )
        with self.assertRaises(ValueError):
            _decision(evidence=rows)

    def test_future_option_quote_is_rejected_at_orchestrator_boundary(self):
        with self.assertRaises(ValueError):
            _decision(contracts=[_contract(observed_at=_t(4, 0, 1))])

    def test_missing_options_context_becomes_no_trade_not_futures_fallback(self):
        result = _decision(evidence=_evidence(include_options=False))
        self.assertEqual(result["decision_record"]["final_decision"], "NO_TRADE")
        self.assertEqual(result["decision_record"]["pipeline_status"], "OPTIONS_CONTEXT_MISSING")
        self.assertIsNone(result["contract_selection"])
        self.assertFalse(result["futures_route_invoked"])

    def test_no_eligible_contract_becomes_no_trade(self):
        result = _decision(contracts=[_contract(spread=20.0)])
        self.assertEqual(result["decision_record"]["final_decision"], "NO_TRADE")
        self.assertEqual(result["decision_record"]["pipeline_status"], "NO_OPTIONS_CONTRACT")
        self.assertIn("SPREAD_TOO_WIDE", result["decision_record"]["reason_codes"])
        self.assertFalse(result["futures_fallback_allowed"])

    def test_missing_underlying_exit_thesis_becomes_no_trade(self):
        result = _decision(underlying_thesis=None)
        self.assertEqual(result["decision_record"]["final_decision"], "NO_TRADE")
        self.assertEqual(result["decision_record"]["pipeline_status"], "UNDERLYING_EXIT_THESIS_MISSING")
        self.assertIsNone(result["risk"])

    def test_missing_risk_policy_becomes_no_trade(self):
        result = _decision(risk_policy=None)
        self.assertEqual(result["decision_record"]["final_decision"], "NO_TRADE")
        self.assertEqual(result["decision_record"]["pipeline_status"], "OPTIONS_RISK_POLICY_MISSING")
        self.assertIsNone(result["risk"])
        self.assertIsNotNone(result["exit_geometry"])

    def test_underlying_thesis_entry_must_match_click_spot(self):
        with self.assertRaises(ValueError):
            _decision(underlying_thesis=_thesis(entry=99_000.0))

    def test_decision_fingerprint_detects_mutation(self):
        result = _decision()
        self.assertTrue(verify_frozen_click_decision(result))
        result["decision_record"]["pipeline_status"] = "MUTATED"
        self.assertFalse(verify_frozen_click_decision(result))

    def test_outcome_attachment_rejects_mutated_decision(self):
        result = _decision()
        result["risk"]["risk_plan"]["quantity"] += 1
        with self.assertRaises(ValueError):
            attach_btc_options_click_outcome(
                decision_result=result,
                experience_policy=BtcExperiencePolicy(4.0, 3.0),
                replay_observations=[],
                replay_costs=BtcOptionsReplayCostSpec(0.0, 0.0, 60),
            )

    def test_no_trade_outcome_builds_missed_move_learning_without_trade_replay(self):
        result = _decision(evidence=_evidence(include_options=False))
        attached = attach_btc_options_click_outcome(
            decision_result=result,
            experience_policy=BtcExperiencePolicy(4.0, 3.0),
            no_trade_forward_prices=[BtcForwardPriceObservation(_t(1) + timedelta(hours=4), 104_000.0)],
        )
        entry = attached["experience_entry"]
        self.assertEqual(entry["outcome_type"], "NO_TRADE_LEARNING")
        self.assertTrue(entry["no_trade_follow_through"]["large_move_missed"])
        self.assertIsNone(attached["replay_result"])
        self.assertTrue(attached["decision_unchanged"])

    def test_trade_outcome_uses_shadow_replay_and_actual_quote(self):
        result = _decision()
        attached = attach_btc_options_click_outcome(
            decision_result=result,
            experience_policy=BtcExperiencePolicy(4.0, 3.0),
            replay_observations=[
                BtcOptionsReplayObservation(_t(5), 103_000.0, option_bid=180.0, option_ask=182.0)
            ],
            replay_costs=BtcOptionsReplayCostSpec(
                time_exit_fee_per_quantity_account=0.0,
                fixed_time_exit_cost_account=0.0,
                max_exit_quote_delay_seconds=60,
            ),
        )
        replay = attached["replay_result"]
        self.assertEqual(replay["status"], "SHADOW_TRADE_CLOSED")
        self.assertTrue(replay["actual_quote_used_for_pnl"])
        self.assertFalse(replay["model_reference_used_as_fill"])
        self.assertEqual(attached["experience_entry"]["outcome_type"], "TRADE_CLOSED")
        self.assertTrue(attached["decision_unchanged"])

    def test_no_trade_cannot_receive_replay_observations(self):
        result = _decision(evidence=_evidence(include_options=False))
        with self.assertRaises(ValueError):
            attach_btc_options_click_outcome(
                decision_result=result,
                experience_policy=BtcExperiencePolicy(4.0, 3.0),
                replay_observations=[BtcOptionsReplayObservation(_t(5), 103_000.0, 180.0)],
            )

    def test_trade_outcome_requires_explicit_replay_costs(self):
        with self.assertRaises(ValueError):
            attach_btc_options_click_outcome(
                decision_result=_decision(),
                experience_policy=BtcExperiencePolicy(4.0, 3.0),
                replay_observations=[BtcOptionsReplayObservation(_t(5), 103_000.0, 180.0)],
                replay_costs=None,
            )

    def test_architecture_contract_is_options_only_and_separates_outcome_phase(self):
        contract = architecture_contract()
        self.assertTrue(contract["point_in_time_bundle_required"])
        self.assertTrue(contract["future_evidence_is_rejected_not_ignored"])
        self.assertTrue(contract["decision_and_outcome_phases_are_separate"])
        self.assertTrue(contract["decision_fingerprint_required_before_outcome_attachment"])
        self.assertFalse(contract["futures_trade_generation_allowed"])
        self.assertFalse(contract["futures_fallback_allowed"])
        self.assertFalse(contract["broker_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
