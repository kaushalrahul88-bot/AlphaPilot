import math

from app.fno_market_brain_v6_holdout_b_evaluation import (
    GATES,
    PRIMARY_HORIZON,
    apply_pre_registered_gates,
    architecture_contract,
    exact_one_sided_binomial_p,
    wilson_interval_pct,
)


def block(actionable, accuracy=60.0, mean=0.2, median=0.1, p=0.02):
    return {
        "actionable": actionable,
        "horizons": {
            PRIMARY_HORIZON: {
                "direction_correct_rate_nonflat_pct": accuracy,
                "mean_directional_return_pct": mean,
                "median_directional_return_pct": median,
                "one_sided_exact_binomial_p_vs_50pct": p,
                "wilson_95pct_accuracy_interval_pct": [50.0, 70.0],
            }
        },
    }


def passing_inputs():
    overall = block(100, accuracy=60.0, mean=0.2, median=0.1, p=0.02)
    by_window = {
        "APRIL_2026": block(50, accuracy=60.0, mean=0.2),
        "MAY_2026": block(50, accuracy=60.0, mean=0.2),
    }
    counts = [13, 13, 13, 13, 12, 12, 12, 12]
    symbols = ["RELIANCE", "TATASTEEL", "ITC", "BAJFINANCE", "LT", "DRREDDY", "ULTRACEMCO", "POWERGRID"]
    by_stock = {
        symbol: block(count, accuracy=60.0, mean=0.1)
        for symbol, count in zip(symbols, counts)
    }
    by_session = {f"2026-04-{day:02d}": block(10) for day in range(1, 11)}
    return overall, by_window, by_stock, by_session


def test_exact_binomial_tail_is_exact_for_small_sample():
    # P[X>=8], X~Binomial(10,.5) = (45+10+1)/1024.
    assert exact_one_sided_binomial_p(8, 10) == 56 / 1024
    assert exact_one_sided_binomial_p(0, 0) is None


def test_wilson_interval_contains_observed_rate():
    interval = wilson_interval_pct(60, 100)
    assert interval is not None
    assert interval[0] < 60.0 < interval[1]
    assert math.isclose(wilson_interval_pct(1, 1)[1], 100.0, abs_tol=1e-4)


def test_all_preregistered_gates_can_pass_without_hidden_criteria():
    result = apply_pre_registered_gates(*passing_inputs())
    assert result["passed"] is True
    assert all(result["gate_flags"].values())
    assert result["details"]["sample_sufficiency"]["minimum_overall"] == 80
    assert result["details"]["directional_accuracy_and_significance"]["minimum_accuracy_pct"] == 58.0


def test_window_accuracy_is_strictly_above_fifty():
    inputs = list(passing_inputs())
    inputs[1]["APRIL_2026"] = block(50, accuracy=50.0, mean=0.2)
    result = apply_pre_registered_gates(*inputs)
    assert result["passed"] is False
    assert result["gate_flags"]["temporal_robustness"] is False


def test_stock_and_session_concentration_caps_are_applied_to_actionable_theses():
    inputs = list(passing_inputs())
    inputs[2]["RELIANCE"] = block(31, accuracy=60.0, mean=0.1)
    result = apply_pre_registered_gates(*inputs)
    assert result["gate_flags"]["concentration_control"] is False

    inputs = list(passing_inputs())
    inputs[3]["2026-04-01"] = block(16)
    result = apply_pre_registered_gates(*inputs)
    assert result["gate_flags"]["concentration_control"] is False


def test_frozen_gate_constants_match_protocol():
    assert GATES == {
        "minimum_actionable_overall": 80,
        "minimum_actionable_each_window": 30,
        "minimum_stocks_meeting_stock_sample": 6,
        "minimum_actionable_per_stock_for_robustness": 8,
        "minimum_nonflat_90m_accuracy_pct": 58.0,
        "maximum_one_sided_binomial_p": 0.05,
        "minimum_windows_accuracy_exclusive_pct": 50.0,
        "minimum_positive_mean_stocks": 5,
        "minimum_stock_accuracy_pct": 45.0,
        "maximum_stock_concentration_pct": 30.0,
        "maximum_session_concentration_pct": 15.0,
    }


def test_evaluator_safety_boundary_blocks_forward_and_derivative_work():
    contract = architecture_contract()
    assert contract["evaluator_frozen_before_outcomes"] is True
    assert contract["effective_deduplicated_decision_used"] is True
    assert contract["registered_gates_changed"] is False
    assert contract["options_read"] is False
    assert contract["futures_read"] is False
    assert contract["forward_test"] is False
    assert contract["live_execution"] is False
    assert contract["capital_committed"] == 0
