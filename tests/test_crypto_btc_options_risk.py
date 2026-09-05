import unittest

from app.crypto_btc_options_risk import (
    BtcOptionsExecutionSpec,
    BtcOptionsRiskPolicy,
    BtcOptionsRiskScenario,
    architecture_contract,
    build_btc_options_risk_plan,
)


def _selection(side="BUY_CALL", ask=100.0, option_type="CALL"):
    return {
        "version": "BTC_OPTIONS_CONTRACT_SELECTOR_V1",
        "asset": "BTC",
        "platform": "COINDCX",
        "instrument_type": "OPTIONS",
        "status": "OPTIONS_CONTRACT_CANDIDATE_SELECTED",
        "side_candidate": side,
        "selected_contract": {
            "symbol": "BTC-TEST-CALL",
            "eligible": True,
            "rejection_reasons": (),
            "score": 90.0,
            "metrics": {
                "platform": "COINDCX",
                "option_type": option_type,
                "ask": ask,
            },
        },
        "trade_generated": False,
        "order_created": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "capital_committed": 0,
    }


def _policy(**overrides):
    values = dict(
        account_equity=100_000.0,
        max_premium_allocation_pct_of_equity=10.0,
        max_planned_loss_pct_of_equity=2.0,
        max_tail_loss_pct_of_equity=10.0,
        min_net_reward_risk=1.5,
        max_premium_allocation_absolute=None,
    )
    values.update(overrides)
    return BtcOptionsRiskPolicy(**values)


def _spec(**overrides):
    values = dict(
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
    values.update(overrides)
    return BtcOptionsExecutionSpec(**values)


def _scenario(**overrides):
    values = dict(
        stop_premium=80.0,
        target_premium=140.0,
        stop_basis="BTC_THESIS_INVALIDATION",
        target_basis="EXPECTED_MOVE_TRANSLATION",
    )
    values.update(overrides)
    return BtcOptionsRiskScenario(**values)


class BtcOptionsRiskTests(unittest.TestCase):
    def test_builds_options_only_risk_plan_without_order(self):
        result = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(),
            execution_spec=_spec(),
            scenario=_scenario(),
        )
        self.assertEqual(result["status"], "OPTIONS_RISK_PLAN_READY")
        self.assertEqual(result["instrument_type"], "OPTIONS")
        self.assertTrue(result["quantity_selected"])
        self.assertFalse(result["trade_generated"])
        self.assertFalse(result["order_created"])
        self.assertFalse(result["futures_route_invoked"])
        self.assertFalse(result["futures_trade_generated"])
        self.assertEqual(result["capital_committed"], 0)

    def test_rejects_futures_route_state(self):
        selection = _selection()
        selection["futures_route_invoked"] = True
        with self.assertRaises(ValueError):
            build_btc_options_risk_plan(
                contract_selection=selection,
                risk_policy=_policy(),
                execution_spec=_spec(),
                scenario=_scenario(),
            )

    def test_no_contract_means_no_risk_plan_and_no_futures_fallback(self):
        selection = _selection()
        selection["status"] = "NO_OPTIONS_CONTRACT"
        selection["selected_contract"] = None
        result = build_btc_options_risk_plan(
            contract_selection=selection,
            risk_policy=_policy(),
            execution_spec=_spec(),
            scenario=_scenario(),
        )
        self.assertEqual(result["status"], "NO_OPTIONS_RISK_PLAN")
        self.assertFalse(result["futures_fallback_allowed"])
        self.assertIsNone(result["risk_plan"])

    def test_call_put_side_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            build_btc_options_risk_plan(
                contract_selection=_selection(side="BUY_PUT", option_type="CALL"),
                risk_policy=_policy(),
                execution_spec=_spec(),
                scenario=_scenario(),
            )

    def test_stop_must_be_below_long_option_entry(self):
        with self.assertRaises(ValueError):
            build_btc_options_risk_plan(
                contract_selection=_selection(),
                risk_policy=_policy(),
                execution_spec=_spec(),
                scenario=_scenario(stop_premium=100.0),
            )

    def test_target_must_be_above_long_option_entry(self):
        with self.assertRaises(ValueError):
            build_btc_options_risk_plan(
                contract_selection=_selection(),
                risk_policy=_policy(),
                execution_spec=_spec(),
                scenario=_scenario(target_premium=100.0),
            )

    def test_planned_stop_is_not_treated_as_guaranteed_max_loss(self):
        result = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(),
            execution_spec=_spec(),
            scenario=_scenario(),
        )
        plan = result["risk_plan"]
        self.assertFalse(plan["planned_stop_is_guaranteed_max_loss"])
        self.assertTrue(plan["tail_loss_models_full_premium_at_risk"])
        self.assertGreater(plan["full_premium_tail_loss"], plan["planned_stop_loss"])

    def test_quantity_is_limited_by_planned_loss_budget(self):
        # Entry 100, stop 80 => 20 planned risk per quantity.
        # 2% of 100,000 = 2,000 => max 100 quantity; other caps are looser.
        result = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(
                max_premium_allocation_pct_of_equity=20.0,
                max_tail_loss_pct_of_equity=20.0,
            ),
            execution_spec=_spec(),
            scenario=_scenario(),
        )
        self.assertEqual(result["risk_plan"]["quantity"], 100.0)
        self.assertEqual(result["risk_plan"]["limiting_constraint"], "planned_stop_risk")

    def test_quantity_can_be_limited_by_premium_allocation(self):
        # 1% premium allocation = 1,000; entry 100 => 10 quantity.
        result = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(max_premium_allocation_pct_of_equity=1.0, max_planned_loss_pct_of_equity=10.0),
            execution_spec=_spec(),
            scenario=_scenario(),
        )
        self.assertEqual(result["risk_plan"]["quantity"], 10.0)
        self.assertIn("premium_allocation", result["risk_plan"]["limiting_constraints"])

    def test_quantity_can_be_limited_by_full_premium_tail_risk(self):
        # Tail budget 1% = 1,000; full premium 100 each => 10 quantity.
        result = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(
                max_premium_allocation_pct_of_equity=20.0,
                max_planned_loss_pct_of_equity=10.0,
                max_tail_loss_pct_of_equity=1.0,
            ),
            execution_spec=_spec(),
            scenario=_scenario(),
        )
        self.assertEqual(result["risk_plan"]["quantity"], 10.0)
        self.assertIn("full_premium_tail_risk", result["risk_plan"]["limiting_constraints"])

    def test_absolute_premium_cap_is_independent_of_commodity_15000_rule(self):
        result = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(
                max_premium_allocation_pct_of_equity=100.0,
                max_planned_loss_pct_of_equity=100.0,
                max_tail_loss_pct_of_equity=100.0,
                max_premium_allocation_absolute=3_000.0,
            ),
            execution_spec=_spec(),
            scenario=_scenario(),
        )
        self.assertEqual(result["risk_plan"]["premium_budget"], 3_000.0)
        self.assertEqual(result["risk_plan"]["quantity"], 30.0)
        self.assertNotEqual(result["risk_plan"]["premium_budget"], 15_000.0)

    def test_quantity_is_floored_to_platform_step(self):
        result = build_btc_options_risk_plan(
            contract_selection=_selection(ask=90.0),
            risk_policy=_policy(
                max_premium_allocation_pct_of_equity=1.0,
                max_planned_loss_pct_of_equity=10.0,
                max_tail_loss_pct_of_equity=10.0,
            ),
            execution_spec=_spec(quantity_step=0.25, min_quantity=0.25),
            scenario=_scenario(stop_premium=70.0, target_premium=130.0),
        )
        self.assertAlmostEqual((result["risk_plan"]["quantity"] / 0.25) % 1, 0.0, places=9)
        self.assertLessEqual(result["risk_plan"]["premium_outlay"], result["risk_plan"]["premium_budget"])

    def test_platform_minimum_quantity_is_rounded_up_to_valid_step(self):
        result = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(
                max_premium_allocation_pct_of_equity=100.0,
                max_planned_loss_pct_of_equity=100.0,
                max_tail_loss_pct_of_equity=100.0,
            ),
            execution_spec=_spec(quantity_step=0.25, min_quantity=0.30, max_quantity=1.0),
            scenario=_scenario(),
        )
        self.assertEqual(result["risk_plan"]["minimum_quantity"], 0.5)
        self.assertGreaterEqual(result["risk_plan"]["quantity"], 0.5)

    def test_platform_minimum_quantity_can_fail_closed(self):
        result = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(
                max_premium_allocation_pct_of_equity=0.05,
                max_planned_loss_pct_of_equity=0.05,
                max_tail_loss_pct_of_equity=0.05,
            ),
            execution_spec=_spec(min_quantity=1.0),
            scenario=_scenario(),
        )
        self.assertEqual(result["status"], "NO_OPTIONS_RISK_PLAN")
        self.assertFalse(result["quantity_selected"])

    def test_costs_reduce_net_reward_risk(self):
        zero_cost = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(min_net_reward_risk=1.0),
            execution_spec=_spec(),
            scenario=_scenario(),
        )
        costed = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(min_net_reward_risk=1.0),
            execution_spec=_spec(
                entry_slippage_pct_of_premium=1.0,
                exit_slippage_pct_of_premium=1.0,
                entry_fee_per_quantity_account=1.0,
                stop_exit_fee_per_quantity_account=1.0,
                target_exit_fee_per_quantity_account=1.0,
            ),
            scenario=_scenario(),
        )
        self.assertLess(costed["risk_plan"]["net_reward_risk"], zero_cost["risk_plan"]["net_reward_risk"])

    def test_low_net_rr_fails_closed_without_switching_instrument(self):
        result = build_btc_options_risk_plan(
            contract_selection=_selection(),
            risk_policy=_policy(min_net_reward_risk=3.0),
            execution_spec=_spec(),
            scenario=_scenario(),
        )
        self.assertEqual(result["status"], "NO_OPTIONS_RISK_PLAN")
        self.assertFalse(result["futures_fallback_allowed"])
        self.assertFalse(result["futures_route_invoked"])

    def test_policy_has_no_implicit_defaults_and_rejects_invalid_percentages(self):
        with self.assertRaises(TypeError):
            BtcOptionsRiskPolicy()
        with self.assertRaises(ValueError):
            _policy(max_planned_loss_pct_of_equity=101.0).validated()

    def test_architecture_contract_preserves_risk_boundaries(self):
        contract = architecture_contract()
        self.assertEqual(contract["default_platform"], "COINDCX")
        self.assertTrue(contract["crypto_risk_policy_must_be_explicit"])
        self.assertFalse(contract["inherits_commodity_15000_rule"])
        self.assertFalse(contract["planned_stop_is_guaranteed_max_loss"])
        self.assertTrue(contract["full_premium_tail_loss_checked_separately"])
        self.assertTrue(contract["quantity_selected_here"])
        self.assertFalse(contract["trade_generated_here"])
        self.assertFalse(contract["futures_fallback_allowed"])
        self.assertFalse(contract["broker_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
