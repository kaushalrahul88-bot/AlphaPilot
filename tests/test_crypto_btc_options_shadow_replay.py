import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_options_shadow_replay import (
    BtcOptionsReplayCostSpec,
    BtcOptionsReplayObservation,
    architecture_contract,
    replay_btc_options_shadow_trade,
)


def _t(hours=0, minutes=0):
    return datetime(2026, 9, 5, 4, 0, tzinfo=timezone.utc) + timedelta(hours=hours, minutes=minutes)


def _risk(side="BUY_CALL", symbol="BTC-TEST", **plan_overrides):
    plan = {
        "contract_symbol": symbol,
        "option_type": "CALL" if side == "BUY_CALL" else "PUT",
        "side_candidate": side,
        "effective_entry_premium_after_slippage": 1_500.0,
        "quantity": 2.0,
        "premium_outlay": 3_000.0,
        "planned_stop_loss": 2_000.0,
        "full_premium_tail_loss": 3_000.0,
        "account_currency": "TEST",
        "premium_currency": "TEST",
        "premium_to_account_rate": 1.0,
        "contract_multiplier": 1.0,
        "cost_model": {
            "exit_slippage_pct_of_premium": 0.0,
            "entry_fee_per_quantity_account": 0.0,
            "stop_exit_fee_per_quantity_account": 0.0,
            "target_exit_fee_per_quantity_account": 0.0,
            "fixed_entry_cost_account": 0.0,
            "fixed_stop_exit_cost_account": 0.0,
            "fixed_target_exit_cost_account": 0.0,
        },
    }
    plan.update(plan_overrides)
    return {
        "version": "BTC_OPTIONS_RISK_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": "OPTIONS_RISK_PLAN_READY",
        "side_candidate": side,
        "selected_contract_symbol": symbol,
        "risk_plan": plan,
        "trade_generated": False,
        "order_created": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
    }


def _geometry(side="BUY_CALL", symbol="BTC-TEST", **overrides):
    values = {
        "version": "BTC_OPTIONS_EXIT_GEOMETRY_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "side_candidate": side,
        "symbol": symbol,
        "status": "OPTIONS_EXIT_GEOMETRY_READY",
        "primary_stop_basis": "UNDERLYING_INVALIDATION",
        "primary_target_basis": "UNDERLYING_TARGET",
        "time_exit_hours": 6.0,
        "invalidation_btc_price": 98_000.0 if side == "BUY_CALL" else 102_000.0,
        "target_btc_price": 103_000.0 if side == "BUY_CALL" else 97_000.0,
        "stop_premium_reference": 500.0,
        "target_premium_reference": 3_000.0,
        "premium_projection_is_forecast": False,
        "actual_quote_required_at_exit": True,
        "trade_generated": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
    }
    values.update(overrides)
    return values


def _costs(**overrides):
    values = dict(
        time_exit_fee_per_quantity_account=0.0,
        fixed_time_exit_cost_account=0.0,
        max_exit_quote_delay_seconds=120,
    )
    values.update(overrides)
    return BtcOptionsReplayCostSpec(**values)


class BtcOptionsShadowReplayTests(unittest.TestCase):
    def test_call_target_closes_on_actual_option_bid(self):
        result = replay_btc_options_shadow_trade(
            decision_at=_t(),
            risk=_risk(),
            geometry=_geometry(target_premium_reference=9_999.0),
            observations=[
                BtcOptionsReplayObservation(_t(hours=1), 101_000.0, 1_800.0),
                BtcOptionsReplayObservation(_t(hours=2), 103_200.0, 2_400.0),
            ],
            replay_costs=_costs(),
        )
        self.assertEqual(result["status"], "SHADOW_TRADE_CLOSED")
        self.assertEqual(result["exit_reason"], "UNDERLYING_TARGET")
        self.assertEqual(result["actual_exit_bid"], 2_400.0)
        self.assertEqual(result["model_premium_reference_at_trigger"], 9_999.0)
        self.assertFalse(result["model_reference_used_as_fill"])
        self.assertTrue(result["actual_quote_used_for_pnl"])
        self.assertEqual(result["net_pnl_account"], 1_800.0)

    def test_call_invalidation_has_precedence_over_time_exit(self):
        result = replay_btc_options_shadow_trade(
            decision_at=_t(),
            risk=_risk(),
            geometry=_geometry(time_exit_hours=2.0),
            observations=[BtcOptionsReplayObservation(_t(hours=2), 97_500.0, 450.0)],
            replay_costs=_costs(),
        )
        self.assertEqual(result["exit_reason"], "UNDERLYING_INVALIDATION")

    def test_time_exit_uses_explicit_time_exit_costs(self):
        result = replay_btc_options_shadow_trade(
            decision_at=_t(),
            risk=_risk(),
            geometry=_geometry(time_exit_hours=2.0),
            observations=[BtcOptionsReplayObservation(_t(hours=2), 100_500.0, 1_600.0)],
            replay_costs=_costs(time_exit_fee_per_quantity_account=5.0, fixed_time_exit_cost_account=10.0),
        )
        self.assertEqual(result["exit_reason"], "TIME_EXIT")
        self.assertEqual(result["gross_pnl_account"], 200.0)
        self.assertEqual(result["total_fees_account"], 20.0)
        self.assertEqual(result["net_pnl_account"], 180.0)

    def test_put_target_direction_is_reversed(self):
        result = replay_btc_options_shadow_trade(
            decision_at=_t(),
            risk=_risk(side="BUY_PUT"),
            geometry=_geometry(side="BUY_PUT"),
            observations=[BtcOptionsReplayObservation(_t(hours=1), 96_900.0, 2_300.0)],
            replay_costs=_costs(),
        )
        self.assertEqual(result["exit_reason"], "UNDERLYING_TARGET")
        self.assertGreater(result["net_pnl_account"], 0)

    def test_no_trigger_in_supplied_window_is_unresolved_not_imputed(self):
        result = replay_btc_options_shadow_trade(
            decision_at=_t(),
            risk=_risk(),
            geometry=_geometry(time_exit_hours=6.0),
            observations=[BtcOptionsReplayObservation(_t(hours=1), 100_500.0, 1_600.0)],
            replay_costs=_costs(),
        )
        self.assertEqual(result["status"], "NO_EXIT_TRIGGER_IN_WINDOW")
        self.assertFalse(result["shadow_trade_closed"])

    def test_missing_quote_at_trigger_can_use_first_quote_inside_explicit_tolerance(self):
        result = replay_btc_options_shadow_trade(
            decision_at=_t(),
            risk=_risk(),
            geometry=_geometry(),
            observations=[
                BtcOptionsReplayObservation(_t(hours=1), 103_100.0, None),
                BtcOptionsReplayObservation(_t(hours=1, minutes=1), 103_050.0, 2_200.0),
            ],
            replay_costs=_costs(max_exit_quote_delay_seconds=90),
        )
        self.assertEqual(result["status"], "SHADOW_TRADE_CLOSED")
        self.assertEqual(result["exit_quote_delay_seconds"], 60.0)
        self.assertEqual(result["actual_exit_bid"], 2_200.0)

    def test_quote_gap_beyond_tolerance_fails_closed(self):
        result = replay_btc_options_shadow_trade(
            decision_at=_t(),
            risk=_risk(),
            geometry=_geometry(),
            observations=[
                BtcOptionsReplayObservation(_t(hours=1), 103_100.0, None),
                BtcOptionsReplayObservation(_t(hours=1, minutes=3), 103_050.0, 2_200.0),
            ],
            replay_costs=_costs(max_exit_quote_delay_seconds=120),
        )
        self.assertEqual(result["status"], "UNRESOLVED_EXIT_QUOTE_GAP")
        self.assertFalse(result["shadow_trade_closed"])

    def test_observations_must_be_strictly_after_decision(self):
        with self.assertRaises(ValueError):
            replay_btc_options_shadow_trade(
                decision_at=_t(),
                risk=_risk(),
                geometry=_geometry(),
                observations=[BtcOptionsReplayObservation(_t(), 100_000.0, 1_500.0)],
                replay_costs=_costs(),
            )

    def test_observations_must_be_chronological(self):
        with self.assertRaises(ValueError):
            replay_btc_options_shadow_trade(
                decision_at=_t(),
                risk=_risk(),
                geometry=_geometry(),
                observations=[
                    BtcOptionsReplayObservation(_t(hours=2), 101_000.0, 1_700.0),
                    BtcOptionsReplayObservation(_t(hours=1), 101_500.0, 1_800.0),
                ],
                replay_costs=_costs(),
            )

    def test_futures_route_state_is_rejected(self):
        risk = _risk()
        risk["futures_route_invoked"] = True
        with self.assertRaises(ValueError):
            replay_btc_options_shadow_trade(
                decision_at=_t(),
                risk=risk,
                geometry=_geometry(),
                observations=[BtcOptionsReplayObservation(_t(hours=1), 103_100.0, 2_200.0)],
                replay_costs=_costs(),
            )

    def test_contract_symbol_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            replay_btc_options_shadow_trade(
                decision_at=_t(),
                risk=_risk(symbol="BTC-A"),
                geometry=_geometry(symbol="BTC-B"),
                observations=[BtcOptionsReplayObservation(_t(hours=1), 103_100.0, 2_200.0)],
                replay_costs=_costs(),
            )

    def test_exit_slippage_is_applied_to_actual_bid_not_model_reference(self):
        risk = _risk()
        risk["risk_plan"]["cost_model"]["exit_slippage_pct_of_premium"] = 10.0
        result = replay_btc_options_shadow_trade(
            decision_at=_t(),
            risk=risk,
            geometry=_geometry(target_premium_reference=8_000.0),
            observations=[BtcOptionsReplayObservation(_t(hours=1), 103_100.0, 2_000.0)],
            replay_costs=_costs(),
        )
        self.assertEqual(result["effective_exit_premium_after_slippage"], 1_800.0)
        self.assertEqual(result["net_pnl_account"], 600.0)

    def test_architecture_contract_forbids_quote_imputation_and_futures_fallback(self):
        contract = architecture_contract()
        self.assertTrue(contract["chronological_replay_required"])
        self.assertTrue(contract["actual_option_bid_required_for_realized_pnl"])
        self.assertFalse(contract["model_premium_reference_used_as_fill"])
        self.assertFalse(contract["quote_gap_imputation_allowed"])
        self.assertFalse(contract["futures_fallback_allowed"])
        self.assertFalse(contract["live_execution"])


if __name__ == "__main__":
    unittest.main()
