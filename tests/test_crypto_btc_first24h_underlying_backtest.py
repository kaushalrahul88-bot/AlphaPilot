from app.crypto_btc_first24h_underlying_backtest import _market_state_from_frozen, _summary


def test_missing_frozen_thesis_fails_closed_to_unknown():
    assert _market_state_from_frozen(None) == {
        "instrument_neutral": True,
        "direction": "UNKNOWN",
        "state": "NO_FROZEN_DIRECTION",
    }


def test_frozen_direction_is_forwarded_without_options_translation():
    frozen = {"decision": {"market_direction": "bullish", "market_state": "COHERENT_DIRECTION_THESIS"}}
    assert _market_state_from_frozen(frozen)["direction"] == "BULLISH"


def test_summary_scores_only_resolved_structural_setups():
    rows = [
        {"setup": {"decision": "BULLISH"}, "outcome": {"status": "T1", "r_multiple": 1.5}},
        {"setup": {"decision": "BEARISH"}, "outcome": {"status": "STOP", "r_multiple": -1.0}},
        {"setup": {"decision": "WAIT"}, "outcome": {"status": "NO_SETUP", "r_multiple": None}},
        {"setup": {"decision": "BULLISH"}, "outcome": {"status": "NO_ENTRY", "r_multiple": None}},
    ]
    result = _summary(rows)
    assert result["directional_setups"] == 3
    assert result["resolved_setups"] == 2
    assert result["setup_win_rate_pct"] == 50.0
    assert result["total_r"] == 0.5
    assert result["options_profitability_evaluated"] is False
