import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_options_contract_selector import (
    BtcOptionContractSnapshot,
    BtcOptionsSelectionPolicy,
    architecture_contract,
    evaluate_contract,
    select_btc_option_contract,
)


def _t(hour=4, minute=0):
    return datetime(2026, 9, 5, hour, minute, tzinfo=timezone.utc)


def _preflight(side="BUY_CALL", allowed=True):
    return {
        "version": "BTC_OPTIONS_PREFLIGHT_V1",
        "asset": "BTC",
        "instrument_type": "OPTIONS",
        "status": "READY_FOR_OPTIONS_CONTRACT_SELECTION" if allowed else "OPTIONS_CONTEXT_MISSING",
        "side_candidate": side,
        "contract_selection_allowed": allowed,
        "trade_generated": False,
        "futures_trade_generated": False,
        "futures_route_invoked": False,
    }


def _contract(
    *,
    symbol="BTC-C-100000",
    option_type="CALL",
    strike=100_000,
    expiry_hours=48,
    observed_at=None,
    bid=950,
    ask=1_000,
    mark=975,
    delta=0.50,
    gamma=0.00002,
    theta=-12.0,
    vega=18.0,
    iv=0.55,
    oi=500,
    volume=300,
    platform="COINDCX",
):
    return BtcOptionContractSnapshot(
        symbol=symbol,
        option_type=option_type,
        strike=strike,
        expiry_at=_t() + timedelta(hours=expiry_hours),
        observed_at=observed_at or (_t() - timedelta(seconds=30)),
        bid=bid,
        ask=ask,
        mark=mark,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        implied_volatility=iv,
        open_interest=oi,
        volume_24h=volume,
        platform=platform,
    )


class BtcOptionsContractSelectorTests(unittest.TestCase):
    def test_call_preflight_selects_call_only(self):
        call = _contract(symbol="CALL-ATM")
        put = _contract(symbol="PUT-ATM", option_type="PUT", delta=-0.5)
        result = select_btc_option_contract(
            options_preflight=_preflight("BUY_CALL"),
            contracts=[put, call],
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.5,
        )
        self.assertEqual(result["status"], "OPTIONS_CONTRACT_CANDIDATE_SELECTED")
        self.assertEqual(result["selected_contract"]["symbol"], "CALL-ATM")
        self.assertEqual(result["side_candidate"], "BUY_CALL")
        self.assertFalse(result["trade_generated"])
        self.assertFalse(result["futures_route_invoked"])

    def test_put_preflight_selects_put_only(self):
        put = _contract(symbol="PUT-ATM", option_type="PUT", delta=-0.52)
        result = select_btc_option_contract(
            options_preflight=_preflight("BUY_PUT"),
            contracts=[put],
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.5,
            expected_holding_hours=6.0,
            fee_bps_per_side=1.0,
        )
        self.assertEqual(result["selected_contract"]["metrics"]["option_type"], "PUT")
        self.assertEqual(result["side_candidate"], "BUY_PUT")

    def test_preflight_block_returns_no_contract_and_never_futures_fallback(self):
        result = select_btc_option_contract(
            options_preflight=_preflight("BUY_CALL", allowed=False),
            contracts=[_contract()],
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
        )
        self.assertEqual(result["status"], "NO_OPTIONS_CONTRACT")
        self.assertIsNone(result["selected_contract"])
        self.assertFalse(result["futures_route_invoked"])
        self.assertFalse(result["futures_trade_generated"])

    def test_selector_rejects_futures_route_state(self):
        preflight = _preflight("BUY_CALL")
        preflight["futures_route_invoked"] = True
        with self.assertRaises(ValueError):
            select_btc_option_contract(
                options_preflight=preflight,
                contracts=[_contract()],
                decision_at=_t(),
                btc_spot_price=100_000,
                expected_move_pct=2.0,
                expected_holding_hours=8.0,
                fee_bps_per_side=1.0,
            )

    def test_future_quote_is_ineligible(self):
        row = evaluate_contract(
            _contract(observed_at=_t() + timedelta(seconds=1)),
            required_option_type="CALL",
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.5,
            policy=BtcOptionsSelectionPolicy(),
        )
        self.assertFalse(row.eligible)
        self.assertIn("FUTURE_QUOTE", row.rejection_reasons)

    def test_stale_quote_is_ineligible(self):
        row = evaluate_contract(
            _contract(observed_at=_t() - timedelta(minutes=3)),
            required_option_type="CALL",
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.5,
            policy=BtcOptionsSelectionPolicy(max_quote_age_seconds=120),
        )
        self.assertFalse(row.eligible)
        self.assertIn("STALE_QUOTE", row.rejection_reasons)

    def test_expiry_must_cover_expected_holding_horizon(self):
        row = evaluate_contract(
            _contract(expiry_hours=9),
            required_option_type="CALL",
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.5,
            policy=BtcOptionsSelectionPolicy(min_expiry_holding_multiple=1.5),
        )
        self.assertFalse(row.eligible)
        self.assertIn("EXPIRY_TOO_CLOSE_FOR_HORIZON", row.rejection_reasons)

    def test_wide_spread_is_rejected(self):
        row = evaluate_contract(
            _contract(bid=800, ask=1_000, mark=900),
            required_option_type="CALL",
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.5,
            policy=BtcOptionsSelectionPolicy(max_spread_pct=8.0),
        )
        self.assertFalse(row.eligible)
        self.assertIn("SPREAD_TOO_WIDE", row.rejection_reasons)

    def test_missing_greeks_fail_closed(self):
        row = evaluate_contract(
            _contract(gamma=None, theta=None, vega=None),
            required_option_type="CALL",
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.5,
            policy=BtcOptionsSelectionPolicy(),
        )
        self.assertFalse(row.eligible)
        self.assertIn("GAMMA_MISSING", row.rejection_reasons)
        self.assertIn("THETA_MISSING", row.rejection_reasons)
        self.assertIn("VEGA_MISSING", row.rejection_reasons)

    def test_non_coindcx_contract_is_rejected_by_default(self):
        row = evaluate_contract(
            _contract(platform="OTHER_EXCHANGE"),
            required_option_type="CALL",
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.5,
            policy=BtcOptionsSelectionPolicy(),
        )
        self.assertFalse(row.eligible)
        self.assertIn("NON_DEFAULT_PLATFORM", row.rejection_reasons)

    def test_tighter_spread_wins_when_other_characteristics_match(self):
        wide = _contract(symbol="WIDE", bid=920, ask=1_000, mark=960)
        tight = _contract(symbol="TIGHT", bid=970, ask=1_000, mark=985)
        result = select_btc_option_contract(
            options_preflight=_preflight("BUY_CALL"),
            contracts=[wide, tight],
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.5,
        )
        self.assertEqual(result["selected_contract"]["symbol"], "TIGHT")

    def test_extreme_iv_penalizes_score_but_does_not_invent_futures_trade(self):
        normal = evaluate_contract(
            _contract(),
            required_option_type="CALL",
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.50,
            policy=BtcOptionsSelectionPolicy(),
        )
        extreme = evaluate_contract(
            _contract(),
            required_option_type="CALL",
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.99,
            policy=BtcOptionsSelectionPolicy(),
        )
        self.assertTrue(normal.eligible)
        self.assertTrue(extreme.eligible)
        self.assertLess(extreme.score, normal.score)

    def test_no_eligible_contract_is_explicit_no_options_contract(self):
        result = select_btc_option_contract(
            options_preflight=_preflight("BUY_CALL"),
            contracts=[_contract(bid=500, ask=1_000, mark=750)],
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
        )
        self.assertEqual(result["status"], "NO_OPTIONS_CONTRACT")
        self.assertIsNone(result["selected_contract"])
        self.assertFalse(result["futures_route_invoked"])

    def test_first_order_response_is_labelled_diagnostic_not_forecast(self):
        row = evaluate_contract(
            _contract(),
            required_option_type="CALL",
            decision_at=_t(),
            btc_spot_price=100_000,
            expected_move_pct=2.0,
            expected_holding_hours=8.0,
            fee_bps_per_side=1.0,
            iv_percentile=0.5,
            policy=BtcOptionsSelectionPolicy(),
        )
        self.assertIn("first_order_premium_response_pct_diagnostic", row.metrics)
        self.assertNotIn("premium_forecast", row.metrics)

    def test_architecture_contract_is_options_only_and_zero_capital(self):
        contract = architecture_contract()
        self.assertEqual(contract["default_platform"], "COINDCX")
        self.assertEqual(contract["instrument_type"], "OPTIONS")
        self.assertTrue(contract["requires_point_in_time_quotes"])
        self.assertTrue(contract["requires_iv_and_greeks"])
        self.assertFalse(contract["capital_rule_defined_here"])
        self.assertFalse(contract["quantity_selected_here"])
        self.assertFalse(contract["futures_route_invoked"])
        self.assertFalse(contract["futures_fallback_allowed"])
        self.assertFalse(contract["trade_generated_here"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
