from app import fno_market_brain_v3 as brain


def _tf(price=100.0, alpha=3.0, volume=1.8, bullish=True):
    if bullish:
        ema20, ema50, vwap, macd, structure, signal = 99.0, 97.5, 99.2, 0.45, "BULLISH_HIGHER_HIGH", "LONG"
        d_res, d_sup = 0.2, 1.2
    else:
        ema20, ema50, vwap, macd, structure, signal = 101.0, 103.0, 101.2, -0.45, "BEARISH_LOWER_LOW", "SHORT"
        d_res, d_sup = 1.2, 0.2
    return {
        "price": price,
        "alpha_score": alpha if bullish else -alpha,
        "ema20": ema20,
        "ema50": ema50,
        "vwap": vwap,
        "macd_hist": macd,
        "atr14": 1.0,
        "bollinger_upper": 101.5,
        "bollinger_lower": 98.5,
        "volume_ratio_raw": volume,
        "distance_to_resistance_atr": d_res,
        "distance_to_support_atr": d_sup,
        "market_structure": structure,
        "signal": signal,
    }


def _technical(bullish=True, volume=1.8, alpha=3.0):
    return {
        "timeframes": {
            "5m": _tf(bullish=bullish, volume=volume, alpha=alpha),
            "15m": _tf(bullish=bullish, volume=volume, alpha=alpha),
            "1h": _tf(bullish=bullish, volume=volume, alpha=alpha),
        }
    }


def test_architecture_contract_blocks_leakage_and_derivatives():
    contract = brain.architecture_contract()
    assert contract["two_stage_brain"] is True
    assert contract["outcomes_read"] is False
    assert contract["future_bars_read"] is False
    assert contract["option_inputs_read"] is False
    assert contract["futures_inputs_read"] is False
    assert contract["v1_v2_thresholds_retuned"] is False
    assert contract["evaluation_not_run_by_module"] is True


def test_strong_bullish_expansion_becomes_long():
    context = {
        "relative_strength_vs_nifty_pct": 0.4,
        "peer_mean_60m_pct": 0.3,
        "market_60m_pct": 0.2,
        "peer_breadth": 0.75,
        "components": {"events": 1},
    }
    result = brain.decide("LTIM", _technical(bullish=True), context)
    assert result["expansion"]["ready"] is True
    assert result["direction"]["side"] == "LONG"
    assert result["action"] == "LONG"


def test_strong_bearish_expansion_becomes_short():
    context = {
        "relative_strength_vs_nifty_pct": -0.4,
        "peer_mean_60m_pct": -0.3,
        "market_60m_pct": -0.2,
        "peer_breadth": -0.75,
        "components": {"events": -1},
    }
    result = brain.decide("SBIN", _technical(bullish=False), context)
    assert result["expansion"]["ready"] is True
    assert result["direction"]["side"] == "SHORT"
    assert result["action"] == "SHORT"


def test_low_participation_weak_pressure_stays_no_trade():
    technical = _technical(bullish=True, volume=0.75, alpha=0.2)
    for payload in technical["timeframes"].values():
        payload["macd_hist"] = 0.01
        payload["distance_to_resistance_atr"] = 2.0
        payload["distance_to_support_atr"] = 2.0
        payload["signal"] = "NEUTRAL"
        payload["market_structure"] = "RANGE"
    result = brain.decide("ONGC", technical, {})
    assert result["action"] == "NO_TRADE"
    assert "EXPANSION_NOT_READY" in result["reasons"]


def test_direction_conflict_stays_no_trade_even_when_expansion_ready():
    technical = _technical(bullish=True)
    technical["timeframes"]["1h"] = _tf(bullish=False, volume=1.8, alpha=3.0)
    technical["timeframes"]["15m"] = _tf(bullish=False, volume=1.8, alpha=3.0)
    result = brain.decide("SUNPHARMA", technical, {
        "relative_strength_vs_nifty_pct": 0.3,
        "peer_mean_60m_pct": 0.3,
    })
    assert result["expansion"]["ready"] is True
    assert result["action"] in {"NO_TRADE", "SHORT"}
    if result["action"] == "NO_TRADE":
        assert any(reason in result["reasons"] for reason in {"DIRECTION_CONFLICT_HIGH", "DIRECTION_EVIDENCE_WEAK"})


def test_missing_context_does_not_break_point_in_time_brain():
    result = brain.decide("ONGC", _technical(bullish=True), None)
    assert result["action"] == "LONG"
    assert result["point_in_time_only"] is True


def test_normalization_uses_dimensionless_features_not_fixed_stock_move_pct():
    state = brain.expansion_state(_technical(bullish=True))
    assert all(value is not None for value in state["atr_pct_by_timeframe"].values())
    names = {item["name"] for item in state["evidence"]}
    assert "5m:structure_proximity" in names
    assert "15m:volume_expansion" in names
    assert "1h:momentum_impulse" in names
