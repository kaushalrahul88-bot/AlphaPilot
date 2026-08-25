import unittest

from app.strategy_regime_routing import (
    _apply_data_quality_status,
    _data_quality_gates,
    evaluate_strategy_regime_router,
)


def _trade(index, timestamp, r_multiple, strategy="PRICE_ACTION_BREAKOUT", grade="CONFIRMED"):
    symbol = ("RELIANCE", "SBIN", "INFY")[index % 3]
    return {
        "strategy": strategy,
        "symbol": symbol,
        "signal_at": timestamp,
        "entry_at": timestamp,
        "option_contract": f"{symbol}-{timestamp}-{index}",
        "r_multiple": r_multiple,
        "option_entry": 100.0,
        "option_stop": 90.0,
        "research_features": {
            "book_price_action": {"price_action_grade": grade},
        },
        "market_brain_features": {
            "breadth_alignment": 0.8,
            "flow_alignment": 0.7,
            "nifty_vwap_alignment": 0.6,
            "bank_vwap_alignment": 0.5,
            "nifty_trend_alignment": 0.7,
            "bank_trend_alignment": 0.5,
            "volatility_expansion": 1.3,
        },
    }


def _development():
    rows = []
    for index in range(32):
        day = 1 + index // 4
        timestamp = f"2026-06-{day:02d}T{10 + index % 4:02d}:00:00+05:30"
        rows.append(_trade(index, timestamp, 1.0 if index % 4 else -1.0))
    return rows


def _holdout(good=True):
    dates = [
        "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31",
        "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
    ]
    rows = []
    for index in range(24):
        timestamp = f"{dates[index % len(dates)]}T{10 + index % 4:02d}:00:00+05:30"
        value = (1.0 if index % 5 else -1.0) if good else (-1.0 if index % 3 else 1.0)
        rows.append(_trade(index + 100, timestamp, value))
    return rows


class StrategyRegimeRoutingTests(unittest.TestCase):
    def test_validates_fixed_route_on_good_untouched_holdout(self):
        result = evaluate_strategy_regime_router(_development(), _holdout(), 0.0)

        self.assertEqual(result["decision"], "VALIDATED_STRATEGY_REGIME_ROUTER")
        self.assertEqual(result["development"]["selected_route_ids"], ["PRICE_ACTION_BREAKOUT|ALIGNED_EXPANSION"])
        self.assertTrue(all(result["acceptance_gates"].values()))
        self.assertFalse(result["production_rules_changed"])
        self.assertFalse(result["live_execution_enabled"])

    def test_rejects_router_when_holdout_economics_fail(self):
        result = evaluate_strategy_regime_router(_development(), _holdout(good=False), 0.0)

        self.assertEqual(result["decision"], "NO_VALIDATED_STRATEGY_REGIME_ROUTER")
        self.assertFalse(result["acceptance_gates"]["holdout_average_r_positive"])

    def test_weak_book_grade_is_fail_closed(self):
        development = _development()
        for row in development:
            row["research_features"]["book_price_action"]["price_action_grade"] = "WEAK"

        result = evaluate_strategy_regime_router(development, _holdout(), 0.0)

        self.assertEqual(result["development"]["selected_route_ids"], [])
        self.assertEqual(result["decision"], "NO_VALIDATED_STRATEGY_REGIME_ROUTER")

    def test_holdout_cannot_change_development_selection(self):
        first = evaluate_strategy_regime_router(_development(), _holdout(), 0.0)
        second = evaluate_strategy_regime_router(_development(), _holdout(good=False), 0.0)

        self.assertEqual(first["development"]["route_candidates"], second["development"]["route_candidates"])
        self.assertEqual(first["development"]["selected_route_ids"], second["development"]["selected_route_ids"])

    def test_rejects_overlapping_trade_identity(self):
        development = _development()
        holdout = [dict(development[0])]

        with self.assertRaisesRegex(ValueError, "overlapping trade identities"):
            evaluate_strategy_regime_router(development, holdout, 0.0)

    def test_data_pipeline_failure_is_not_reported_as_economic_rejection(self):
        result = evaluate_strategy_regime_router(_development(), _holdout(), 0.0)
        development_source = {
            "option_trade_count": 0,
            "market_context_observations": 0,
            "context_match": {"matched_trades": 0, "match_rate_pct": 0.0},
        }
        holdout_source = {
            "option_trade_count": 200,
            "market_context_observations": 0,
            "context_match": {"matched_trades": 0, "match_rate_pct": 0.0},
        }

        _apply_data_quality_status(result, development_source, holdout_source)

        self.assertEqual(result["decision"], "INSUFFICIENT_DATA_FOR_STRATEGY_REGIME_ROUTER")
        self.assertEqual(result["data_quality_status"], "INCOMPLETE")
        self.assertEqual(result["economic_evaluation_status"], "NOT_EVALUABLE")
        self.assertIn("development_option_trades_at_least_30", result["failed_gates"])

    def test_complete_data_preserves_economic_decision(self):
        source = {
            "option_trade_count": 40,
            "market_context_observations": 100,
            "context_match": {"matched_trades": 35, "match_rate_pct": 87.5},
        }
        self.assertTrue(all(_data_quality_gates(source, source).values()))
        result = evaluate_strategy_regime_router(_development(), _holdout(), 0.0)

        _apply_data_quality_status(result, source, source)

        self.assertEqual(result["decision"], "VALIDATED_STRATEGY_REGIME_ROUTER")
        self.assertEqual(result["economic_evaluation_status"], "VALID_SAMPLE")


if __name__ == "__main__":
    unittest.main()
