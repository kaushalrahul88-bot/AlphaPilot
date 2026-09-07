from __future__ import annotations

from unittest.mock import patch

import pytest

from app import fno_market_brain_v4 as brain


def _payload(side: str = "NEUTRAL", *, expansion: bool = True):
    side = side.upper()
    if side == "LONG":
        alpha = 58.0
        price, ema20, ema50, vwap = 102.0, 101.0, 100.0, 101.0
        macd_hist = 0.35
        structure = "UPTREND_HIGHER_HIGH"
        signal = "LONG"
    elif side == "SHORT":
        alpha = 42.0
        price, ema20, ema50, vwap = 98.0, 99.0, 100.0, 99.0
        macd_hist = -0.35
        structure = "DOWNTREND_LOWER_LOW"
        signal = "SHORT"
    else:
        alpha = 50.0
        price = ema20 = ema50 = vwap = 100.0
        macd_hist = 0.0
        structure = "RANGE"
        signal = "NO_TRADE"

    if expansion:
        volume_ratio = 1.75
        upper, lower = price + 1.5, price - 1.5
        d_res = d_sup = 0.2
    else:
        volume_ratio = 1.0
        upper, lower = price + 3.0, price - 3.0
        d_res = d_sup = 2.0

    return {
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "vwap": vwap,
        "alpha_score": alpha,
        "macd_hist": macd_hist,
        "atr14": 1.0,
        "bollinger_upper": upper,
        "bollinger_lower": lower,
        "volume_ratio_raw": volume_ratio,
        "distance_to_resistance_atr": d_res,
        "distance_to_support_atr": d_sup,
        "market_structure": structure,
        "signal": signal,
    }


def _technical(side: str = "NEUTRAL", *, expansion: bool = True):
    return {
        "timeframes": {
            "5m": _payload(side, expansion=expansion),
            "15m": _payload(side, expansion=expansion),
            "1h": _payload(side, expansion=expansion),
        }
    }


def test_alpha_semantics_are_centered_on_50_and_preserve_42_58_boundaries():
    assert brain._alpha_signal(50) == pytest.approx(0.0)
    assert brain._alpha_signal(58) == pytest.approx(1.0)
    assert brain._alpha_signal(42) == pytest.approx(-1.0)
    assert brain._alpha_signal(70) == pytest.approx(1.0)
    assert brain._alpha_signal(30) == pytest.approx(-1.0)


def test_bearish_legacy_alpha_is_bearish_not_bullish():
    technical = {
        "timeframes": {
            "5m": {"alpha_score": 30.0},
            "15m": {"alpha_score": 30.0},
            "1h": {"alpha_score": 30.0},
        }
    }
    direction = brain.direction_state(technical)
    assert direction["side"] == "SHORT"
    assert direction["signed_score"] < 0
    alpha_evidence = [item for item in direction["evidence"] if item["name"].endswith(":alpha")]
    assert alpha_evidence
    assert all(item["value"] == -1.0 for item in alpha_evidence)


def test_neutral_alpha_does_not_create_false_expansion_pressure():
    state = brain.expansion_state(_technical("NEUTRAL", expansion=False))
    assert state["ready"] is False
    assert state["score"] == pytest.approx(0.0)
    pressure = [item for item in state["evidence"] if item["name"].endswith(":technical_pressure")]
    assert pressure
    assert all(item["value"] == 0.0 for item in pressure)


def test_strong_bull_and_bear_states_remain_actionable():
    long_result = brain.decide("TEST", _technical("LONG", expansion=True))
    short_result = brain.decide("TEST", _technical("SHORT", expansion=True))

    assert long_result["action"] == "LONG"
    assert short_result["action"] == "SHORT"
    assert long_result["expansion"]["ready"] is True
    assert short_result["expansion"]["ready"] is True
    assert long_result["direction"]["dominance_ratio"] == pytest.approx(1.0)
    assert short_result["direction"]["dominance_ratio"] == pytest.approx(1.0)


def test_dominance_is_scale_free_and_not_duplicate_of_signed_score():
    assert brain._dominance_ratio(6.0, 4.0) == pytest.approx(0.20)
    assert brain._dominance_ratio(3.0, 2.0) == pytest.approx(0.20)
    assert brain._dominance_ratio(5.9, 4.1) < brain.DIRECTION_DOMINANCE_MIN

    evidence = [
        brain.Evidence("bull", 1.0, 10.0, 6.0),
        brain.Evidence("bear", -1.0, 10.0, -4.0),
    ]
    aggregate = brain._direction_aggregate(evidence)
    assert aggregate["signed_score"] == pytest.approx(2.0)
    assert aggregate["dominance"] == pytest.approx(0.20)


def test_decision_rejects_high_magnitude_but_conflicted_direction():
    with patch.object(brain, "expansion_state", return_value={"ready": True}), patch.object(
        brain,
        "direction_state",
        return_value={
            "side": "LONG",
            "signed_score": 4.0,
            "dominance_ratio": 0.10,
        },
    ):
        result = brain.decide("TEST", {})

    assert result["action"] == "NO_TRADE"
    assert "DIRECTION_CONFLICT_HIGH" in result["reasons"]
    assert "DIRECTION_EVIDENCE_WEAK" not in result["reasons"]


def test_point_in_time_event_is_secondary_and_cannot_create_trade_by_itself():
    context = {"components": {"events": 2}}
    direction = brain.direction_state({}, context)
    assert direction["side"] == "LONG"
    assert direction["signed_score"] == pytest.approx(0.25)
    assert direction["signed_score"] < brain.DIRECTION_ACTION_SCORE


def test_missing_context_is_safe():
    result = brain.decide("TEST", _technical("LONG", expansion=True), None)
    assert result["action"] == "LONG"


def test_architecture_contract_is_outcome_blind_and_derivative_free():
    contract = brain.architecture_contract()
    assert contract["previous_version_frozen"] == "FNO_MARKET_BRAIN_V3_2026-09-07"
    assert contract["alpha_semantics"] == "CENTER_50_FULL_SCALE_AT_42_58"
    assert contract["direction_conflict_metric"] == "INDEPENDENT_WEIGHTED_DOMINANCE_RATIO"
    assert contract["outcomes_read"] is False
    assert contract["future_bars_read"] is False
    assert contract["option_inputs_read"] is False
    assert contract["futures_inputs_read"] is False
    assert contract["benchmark_results_used_in_decision"] is False
    assert contract["thresholds_fit_to_v1_v2_v3_results"] is False
    assert contract["historical_evaluation_run_by_module"] is False
    assert contract["live_execution"] is False
    assert contract["capital_committed"] == 0
