import unittest
from datetime import datetime, timedelta, timezone

from app.risk_discipline import (
    ClosedTrade,
    ControlledLiveEvidence,
    OpenPositionRisk,
    OperationalGates,
    ProposedTrade,
    RiskDisciplineRequest,
    RiskPolicy,
    evaluate_risk_discipline,
)


NOW = datetime(2026, 8, 25, 5, 0, tzinfo=timezone.utc)


def passing_gates():
    return OperationalGates(
        account_state_verified=True,
        executable_nse_session=True,
        fresh_intraday_candles=True,
        universe_scan_complete=True,
        fno_confirmation_complete=True,
        quality_checks_complete=True,
        liquidity_passed=True,
    )


def proposed(**overrides):
    values = {
        "symbol": "RELIANCE",
        "option_type": "CE",
        "correlation_group": "NIFTY_LARGE_CAP",
        "entry_price": 100,
        "stop_price": 90,
        "target_price": 117,
        "lot_size": 25,
        "estimated_cost_rupees": 50,
    }
    values.update(overrides)
    return ProposedTrade(**values)


def request(**overrides):
    values = {
        "capital_rupees": 100_000,
        "proposed_trade": proposed(),
        "operational_gates": passing_gates(),
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return RiskDisciplineRequest(**values)


class RiskDisciplineTests(unittest.TestCase):
    def test_sizes_down_to_whole_lots_and_includes_costs(self):
        result = evaluate_risk_discipline(request())

        self.assertEqual(result["decision"], "ALLOW_PAPER")
        self.assertEqual(result["final_action"], "PAPER_TRADE_ONLY")
        self.assertEqual(result["position_sizing"]["max_quantity"], 75)
        self.assertEqual(result["position_sizing"]["max_lots"], 3)
        self.assertEqual(result["position_sizing"]["potential_loss_rupees"], 800)
        self.assertFalse(result["live_execution_enabled"])

    def test_low_risk_reward_is_no_trade(self):
        result = evaluate_risk_discipline(request(proposed_trade=proposed(target_price=114)))

        self.assertEqual(result["final_action"], "NO_TRADE")
        self.assertIn("MINIMUM_RISK_REWARD_NOT_MET", result["blockers"])

    def test_daily_loss_limit_hard_locks(self):
        trades = [ClosedTrade(pnl_rupees=-3_000, closed_at=NOW - timedelta(minutes=15))]
        result = evaluate_risk_discipline(request(closed_trades=trades))

        self.assertIn("DAILY_LOSS_LOCKED", result["blockers"])
        self.assertEqual(result["budgets"]["available_trade_risk_rupees"], 0)
        self.assertEqual(result["position_sizing"]["max_quantity"], 0)

    def test_three_losses_activate_cooldown(self):
        trades = [
            ClosedTrade(pnl_rupees=-200, closed_at=NOW - timedelta(minutes=45)),
            ClosedTrade(pnl_rupees=-200, closed_at=NOW - timedelta(minutes=30)),
            ClosedTrade(pnl_rupees=-200, closed_at=NOW - timedelta(minutes=10)),
        ]
        result = evaluate_risk_discipline(request(closed_trades=trades))

        self.assertEqual(result["risk_state"]["consecutive_losses"], 3)
        self.assertIn("CONSECUTIVE_LOSS_COOLDOWN_ACTIVE", result["blockers"])
        self.assertIsNotNone(result["risk_state"]["cooldown_until"])

    def test_concurrent_and_correlated_exposure_block(self):
        positions = [
            OpenPositionRisk(symbol="HDFCBANK", correlation_group="NIFTY_LARGE_CAP", risk_rupees=700, current_value_rupees=10_000),
            OpenPositionRisk(symbol="ICICIBANK", correlation_group="NIFTY_LARGE_CAP", risk_rupees=300, current_value_rupees=10_000),
        ]
        result = evaluate_risk_discipline(request(open_positions=positions))

        self.assertIn("MAX_CONCURRENT_POSITIONS_REACHED", result["blockers"])
        self.assertIn("MAX_CORRELATED_RISK_REACHED", result["blockers"])

    def test_existing_open_risk_reduces_available_trade_budget(self):
        positions = [OpenPositionRisk(symbol="TCS", correlation_group="IT", risk_rupees=2_500, current_value_rupees=10_000)]
        result = evaluate_risk_discipline(request(open_positions=positions))

        self.assertEqual(result["budgets"]["available_trade_risk_rupees"], 500)
        self.assertEqual(result["position_sizing"]["max_quantity"], 25)

    def test_weekly_loss_limit_hard_locks_across_sessions(self):
        trades = [ClosedTrade(pnl_rupees=-6_000, closed_at=NOW - timedelta(days=1))]
        result = evaluate_risk_discipline(request(closed_trades=trades))

        self.assertEqual(result["risk_state"]["daily_loss_rupees"], 0)
        self.assertIn("WEEKLY_LOSS_LOCKED", result["blockers"])

    def test_closed_trade_drawdown_hard_locks(self):
        trades = [
            ClosedTrade(pnl_rupees=1_000, closed_at=NOW - timedelta(days=2)),
            ClosedTrade(pnl_rupees=-9_000, closed_at=NOW - timedelta(days=1)),
        ]
        result = evaluate_risk_discipline(request(closed_trades=trades))

        self.assertEqual(result["risk_state"]["max_drawdown_pct"], 9.0)
        self.assertIn("MAX_DRAWDOWN_LOCKED", result["blockers"])

    def test_gross_exposure_limit_hard_locks(self):
        positions = [OpenPositionRisk(symbol="TCS", correlation_group="IT", risk_rupees=100, current_value_rupees=50_000)]
        result = evaluate_risk_discipline(request(open_positions=positions))

        self.assertIn("MAX_GROSS_EXPOSURE_REACHED", result["blockers"])

    def test_single_position_value_cap_can_limit_quantity(self):
        narrow_risk = proposed(stop_price=99, target_price=103)
        result = evaluate_risk_discipline(request(proposed_trade=narrow_risk))

        self.assertEqual(result["position_sizing"]["max_quantity"], 200)
        self.assertEqual(result["position_sizing"]["position_value_rupees"], 20_000)

    def test_operational_gate_failure_is_no_trade(self):
        gates = passing_gates()
        gates.fresh_intraday_candles = False
        result = evaluate_risk_discipline(request(operational_gates=gates))

        self.assertIn("STALE_INTRADAY_CANDLES", result["blockers"])
        self.assertEqual(result["final_action"], "NO_TRADE")

    def test_open_position_without_defined_risk_is_blocked(self):
        positions = [OpenPositionRisk(symbol="TCS", correlation_group="NIFTY_LARGE_CAP", risk_rupees=0, current_value_rupees=20_000)]
        result = evaluate_risk_discipline(request(open_positions=positions, proposed_trade=proposed(correlation_group="IT")))

        self.assertIn("OPEN_POSITION_RISK_UNDEFINED", result["blockers"])

    def test_requested_quantity_above_max_is_blocked(self):
        result = evaluate_risk_discipline(request(proposed_trade=proposed(requested_quantity=100)))

        self.assertIn("REQUESTED_QUANTITY_EXCEEDS_MAX", result["blockers"])
        self.assertIn("PROPOSED_RISK_EXCEEDS_AVAILABLE_BUDGET", result["blockers"])

    def test_controlled_live_is_preview_only_even_when_all_evidence_passes(self):
        evidence = ControlledLiveEvidence(
            paper_trades=40,
            clean_paper_sessions=12,
            expectancy_r=0.2,
            profit_factor=1.4,
            max_drawdown_r=4.5,
            manual_approval_recorded=True,
        )
        result = evaluate_risk_discipline(request(mode="CONTROLLED_LIVE_PREVIEW", controlled_live_evidence=evidence))

        self.assertTrue(result["controlled_live_preview_eligible"])
        self.assertFalse(result["live_execution_enabled"])
        self.assertEqual(result["final_action"], "NO_TRADE")
        self.assertIn("LIVE_EXECUTION_DISABLED_V1", result["blockers"])

    def test_controlled_live_preview_lists_missing_evidence(self):
        result = evaluate_risk_discipline(request(mode="CONTROLLED_LIVE_PREVIEW"))

        self.assertFalse(result["controlled_live_preview_eligible"])
        self.assertIn("ARMING_MIN_30_PAPER_TRADES_FAILED", result["blockers"])
        self.assertIn("ARMING_MANUAL_APPROVAL_RECORDED_FAILED", result["blockers"])

    def test_policy_rejects_risk_above_frozen_one_percent_cap(self):
        with self.assertRaises(ValueError):
            RiskPolicy(max_risk_per_trade_pct=1.01)

    def test_unknown_contract_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            RiskPolicy(unreviewed_override=True)


if __name__ == "__main__":
    unittest.main()
