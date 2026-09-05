import unittest

from app.crypto_btc_options_exit_geometry import (
    BtcOptionsGreekConvention,
    BtcOptionsUnderlyingThesis,
    architecture_contract,
    build_btc_options_exit_geometry,
)
from app.crypto_btc_options_risk import (
    BtcOptionsExecutionSpec,
    BtcOptionsRiskPolicy,
    BtcOptionsRiskScenario,
    build_btc_options_risk_plan,
)


def _selection(side="BUY_CALL", option_type="CALL", delta=0.50, **metric_overrides):
    metrics = {
        "platform": "COINDCX",
        "option_type": option_type,
        "strike": 100_000.0,
        "ask": 1_500.0,
        "reference_premium": 1_475.0,
        "delta": delta,
        "gamma": 0.00002,
        "theta": -30.0,
        "vega": 10.0,
        "implied_volatility": 55.0,
    }
    metrics.update(metric_overrides)
    return {
        "version": "BTC_OPTIONS_CONTRACT_SELECTOR_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": "OPTIONS_CONTRACT_CANDIDATE_SELECTED",
        "side_candidate": side,
        "selected_contract": {
            "symbol": "BTC-TEST",
            "eligible": True,
            "rejection_reasons": (),
            "score": 90.0,
            "metrics": metrics,
        },
        "trade_generated": False,
        "order_created": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "capital_committed": 0,
    }


def _call_thesis(**overrides):
    values = dict(
        entry_btc_price=100_000.0,
        invalidation_btc_price=98_000.0,
        target_btc_price=103_000.0,
        expected_holding_hours=6.0,
        stop_time_hours=2.0,
        target_time_hours=4.0,
        stop_iv_change_points=0.0,
        target_iv_change_points=0.0,
        iv_stress_points=5.0,
    )
    values.update(overrides)
    return BtcOptionsUnderlyingThesis(**values)


def _put_thesis(**overrides):
    values = dict(
        entry_btc_price=100_000.0,
        invalidation_btc_price=102_000.0,
        target_btc_price=97_000.0,
        expected_holding_hours=6.0,
        stop_time_hours=2.0,
        target_time_hours=4.0,
        stop_iv_change_points=0.0,
        target_iv_change_points=0.0,
        iv_stress_points=5.0,
    )
    values.update(overrides)
    return BtcOptionsUnderlyingThesis(**values)


class BtcOptionsExitGeometryTests(unittest.TestCase):
    def test_call_geometry_translates_underlying_thesis_to_premium_references(self):
        result = build_btc_options_exit_geometry(
            contract_selection=_selection(),
            thesis=_call_thesis(),
        )
        self.assertEqual(result["status"], "OPTIONS_EXIT_GEOMETRY_READY")
        self.assertEqual(result["primary_stop_basis"], "UNDERLYING_INVALIDATION")
        self.assertLess(result["stop_premium_reference"], result["entry_premium_reference"])
        self.assertGreater(result["target_premium_reference"], result["entry_premium_reference"])
        self.assertFalse(result["premium_projection_is_forecast"])
        self.assertFalse(result["premium_reference_is_guaranteed_fill"])
        self.assertTrue(result["actual_quote_required_at_exit"])

    def test_put_geometry_uses_correct_direction(self):
        result = build_btc_options_exit_geometry(
            contract_selection=_selection(side="BUY_PUT", option_type="PUT", delta=-0.50),
            thesis=_put_thesis(),
        )
        self.assertLess(result["stop_premium_reference"], result["entry_premium_reference"])
        self.assertGreater(result["target_premium_reference"], result["entry_premium_reference"])

    def test_call_thesis_geometry_must_be_invalidation_below_entry_below_target(self):
        with self.assertRaises(ValueError):
            build_btc_options_exit_geometry(
                contract_selection=_selection(),
                thesis=_call_thesis(invalidation_btc_price=101_000.0),
            )

    def test_put_thesis_geometry_must_be_target_below_entry_below_invalidation(self):
        with self.assertRaises(ValueError):
            build_btc_options_exit_geometry(
                contract_selection=_selection(side="BUY_PUT", option_type="PUT", delta=-0.50),
                thesis=_put_thesis(target_btc_price=101_000.0),
            )

    def test_options_only_and_no_futures_state_allowed(self):
        selection = _selection()
        selection["futures_route_invoked"] = True
        with self.assertRaises(ValueError):
            build_btc_options_exit_geometry(contract_selection=selection, thesis=_call_thesis())

    def test_side_and_option_type_must_match(self):
        with self.assertRaises(ValueError):
            build_btc_options_exit_geometry(
                contract_selection=_selection(side="BUY_CALL", option_type="PUT", delta=-0.50),
                thesis=_call_thesis(),
            )

    def test_missing_greeks_fail_closed(self):
        with self.assertRaises(ValueError):
            build_btc_options_exit_geometry(
                contract_selection=_selection(gamma=None),
                thesis=_call_thesis(),
            )

    def test_explicit_greek_units_are_required(self):
        with self.assertRaises(ValueError):
            build_btc_options_exit_geometry(
                contract_selection=_selection(),
                thesis=_call_thesis(),
                greek_convention=BtcOptionsGreekConvention(theta_per_day=False, vega_per_iv_point=True),
            )

    def test_iv_stress_makes_conservative_reference_no_higher_than_center(self):
        result = build_btc_options_exit_geometry(
            contract_selection=_selection(),
            thesis=_call_thesis(iv_stress_points=8.0),
        )
        self.assertLessEqual(
            result["target_band"]["conservative_premium"],
            result["target_band"]["assumed"]["premium"],
        )
        self.assertLessEqual(
            result["stop_band"]["conservative_premium"],
            result["stop_band"]["assumed"]["premium"],
        )

    def test_large_underlying_move_sets_local_approximation_warning(self):
        result = build_btc_options_exit_geometry(
            contract_selection=_selection(),
            thesis=_call_thesis(target_btc_price=108_000.0),
        )
        self.assertTrue(result["local_approximation_warning"])

    def test_premium_reference_is_not_primary_stop_trigger(self):
        result = build_btc_options_exit_geometry(
            contract_selection=_selection(),
            thesis=_call_thesis(),
        )
        self.assertFalse(result["premium_stop_is_primary_execution_trigger"])
        self.assertEqual(result["risk_scenario"]["stop_basis"], "BTC_THESIS_INVALIDATION_TRANSLATED_CONSERVATIVELY")

    def test_geometry_feeds_risk_engine_without_generating_order(self):
        geometry = build_btc_options_exit_geometry(
            contract_selection=_selection(),
            thesis=_call_thesis(),
        )
        rs = geometry["risk_scenario"]
        risk = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=BtcOptionsRiskPolicy(
                account_equity=100_000.0,
                max_premium_allocation_pct_of_equity=10.0,
                max_planned_loss_pct_of_equity=2.0,
                max_tail_loss_pct_of_equity=10.0,
                min_net_reward_risk=1.0,
            ),
            execution_spec=BtcOptionsExecutionSpec(
                account_currency="TEST",
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
            ),
            scenario=BtcOptionsRiskScenario(
                stop_premium=rs["stop_premium"],
                target_premium=rs["target_premium"],
                stop_basis=rs["stop_basis"],
                target_basis=rs["target_basis"],
            ),
        )
        self.assertEqual(risk["status"], "OPTIONS_RISK_PLAN_READY")
        self.assertFalse(risk["trade_generated"])
        self.assertFalse(risk["order_created"])
        self.assertFalse(risk["futures_route_invoked"])

    def test_architecture_contract_keeps_outputs_research_only(self):
        contract = architecture_contract()
        self.assertEqual(contract["instrument_type"], "OPTIONS")
        self.assertEqual(contract["primary_stop_basis"], "UNDERLYING_INVALIDATION")
        self.assertFalse(contract["premium_stop_is_arbitrary_percent"])
        self.assertFalse(contract["premium_projection_is_forecast"])
        self.assertFalse(contract["futures_fallback_allowed"])
        self.assertFalse(contract["broker_execution_enabled"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
