import unittest

from app.pullback_short_option_h1 import (
    HOLDOUT_END,
    HOLDOUT_START,
    PROTOCOL_REVISION,
    ROUND_TRIP_COST_BPS,
    SYMBOLS,
    _frozen_one_r_view,
    evaluate_pullback_short_option_h1,
)


def _trades(good=True):
    dates = [
        "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14",
        "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20",
    ]
    rows = []
    for index in range(24):
        symbol = SYMBOLS[index % 4]
        value = (1.0 if index % 5 else -1.0) if good else (-1.0 if index % 3 else 1.0)
        rows.append({
            "strategy": "PULLBACK_CONTINUATION_SHORT",
            "symbol": symbol,
            "direction": "SHORT",
            "action": "BUY PE",
            "signal_at": f"{dates[index % len(dates)]}T10:{(index % 6) * 5:02d}:00+05:30",
            "option_contract": f"{symbol}-PE-{index}",
            "option_entry": 100.0,
            "option_stop": 80.0,
            "r_multiple": value,
            "research_features": {"book_price_action": {"price_action_grade": "WEAK"}},
        })
    return rows


class PullbackShortOptionH1Tests(unittest.TestCase):
    def test_protocol_is_date_and_universe_locked(self):
        self.assertEqual(HOLDOUT_START, "2026-08-11")
        self.assertEqual(HOLDOUT_END, "2026-08-21")
        self.assertEqual(len(SYMBOLS), 8)
        self.assertEqual(ROUND_TRIP_COST_BPS, 10.0)
        self.assertIn("FROZEN_2026_08_25", PROTOCOL_REVISION)

    def test_validates_good_cost_adjusted_option_sample(self):
        result = evaluate_pullback_short_option_h1(_trades(), 24, 24)

        self.assertEqual(result["decision"], "VALIDATED_PULLBACK_SHORT_OPTION_CANDIDATE")
        self.assertTrue(all(result["acceptance_gates"].values()))
        self.assertFalse(result["production_rules_changed"])
        self.assertFalse(result["paper_trading_permission_changed"])
        self.assertFalse(result["live_execution_enabled"])

    def test_rejects_complete_sample_when_economics_fail(self):
        result = evaluate_pullback_short_option_h1(_trades(good=False), 24, 24)

        self.assertEqual(result["decision"], "NO_VALIDATED_PULLBACK_SHORT_OPTION_EDGE")
        self.assertEqual(result["economic_evaluation_status"], "VALID_SAMPLE")
        self.assertFalse(result["economic_gates"]["holdout_average_r_positive"])

    def test_incomplete_replay_is_data_failure_not_economic_rejection(self):
        result = evaluate_pullback_short_option_h1(_trades()[:10], 24, 24)

        self.assertEqual(result["decision"], "INSUFFICIENT_DATA_FOR_PULLBACK_SHORT_OPTION_H1")
        self.assertEqual(result["economic_evaluation_status"], "NOT_EVALUABLE")
        self.assertFalse(result["data_quality_gates"]["resolved_option_trades_at_least_20"])

    def test_diagnostics_cannot_change_frozen_decision(self):
        first = evaluate_pullback_short_option_h1(
            _trades(), 24, 24,
            book_diagnostics=[{"label": "WEAK"}],
            market_brain_diagnostics={"role": "DIAGNOSTIC_ONLY", "by_regime": [{"label": "CONFLICTED"}]},
        )
        second = evaluate_pullback_short_option_h1(
            _trades(), 24, 24,
            book_diagnostics=[{"label": "CONFIRMED"}],
            market_brain_diagnostics={"role": "DIAGNOSTIC_ONLY", "by_regime": [{"label": "ALIGNED_EXPANSION"}]},
        )

        self.assertEqual(first["decision"], second["decision"])
        self.assertEqual(first["holdout_metrics"], second["holdout_metrics"])

    def test_one_r_scenario_overrides_multi_target_replay(self):
        replay = {
            "status": "REPLAY_COMPLETE",
            "option_target1": 130.0,
            "option_target2": 150.0,
            "outcome": "T2",
            "r_multiple": 2.0,
            "target_scenarios": {
                "1.0R": {
                    "target_price": 120.0,
                    "outcome": "T1",
                    "r_multiple": 1.0,
                    "exit_price": 120.0,
                    "exit_at": "2026-08-11T10:15:00+05:30",
                    "ambiguous": False,
                }
            },
        }

        frozen = _frozen_one_r_view(replay)

        self.assertEqual(frozen["r_multiple"], 1.0)
        self.assertEqual(frozen["option_target1"], 120.0)
        self.assertIsNone(frozen["option_target2"])
        self.assertEqual(frozen["frozen_exit_model"], "1.0R_TARGET_OR_PREMIUM_STOP_OR_EOD")


if __name__ == "__main__":
    unittest.main()
