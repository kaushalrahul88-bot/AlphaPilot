from app.crypto_btc_first24h_underlying_backtest import (
    _market_state_from_frozen,
    _structure_prefetch_hours,
    _summary,
)
from app.crypto_btc_prospective_proof_bridge import ProspectiveBtcProofBridgePolicy


def test_missing_frozen_thesis_fails_closed_to_unknown():
    assert _market_state_from_frozen(None) == {
        "instrument_neutral": True,
        "direction": "UNKNOWN",
        "state": "NO_FROZEN_DIRECTION",
    }


def test_frozen_direction_is_forwarded_without_options_translation():
    frozen = {"decision": {"market_direction": "bullish", "market_state": "COHERENT_DIRECTION_THESIS"}}
    assert _market_state_from_frozen(frozen)["direction"] == "BULLISH"


def test_default_prefetch_covers_seven_day_memory_plus_feature_anchor_and_slack():
    policy = ProspectiveBtcProofBridgePolicy().validated()
    assert policy.historical_memory_lookback_hours == 168
    assert _structure_prefetch_hours(policy) == 194


def test_prefetch_expands_with_memory_policy_without_using_future_bars():
    policy = ProspectiveBtcProofBridgePolicy(historical_memory_lookback_hours=240).validated()
    assert _structure_prefetch_hours(policy) == 266


def test_summary_scores_only_resolved_structural_setups_and_reports_evidence_coverage():
    rows = [
        {
            "setup": {"decision": "BULLISH"},
            "outcome": {"status": "T1", "r_multiple": 1.5},
            "derivatives_evidence_status": "BULLISH",
            "historical_memory_available": True,
        },
        {
            "setup": {"decision": "BEARISH"},
            "outcome": {"status": "STOP", "r_multiple": -1.0},
            "derivatives_evidence_status": "BEARISH",
            "historical_memory_available": True,
        },
        {
            "setup": {"decision": "WAIT"},
            "outcome": {"status": "NO_SETUP", "r_multiple": None},
            "derivatives_evidence_status": "UNKNOWN",
            "historical_memory_available": False,
        },
        {
            "setup": {"decision": "BULLISH"},
            "outcome": {"status": "NO_ENTRY", "r_multiple": None},
            "historical_memory_available": False,
        },
    ]
    result = _summary(rows)
    assert result["directional_setups"] == 3
    assert result["resolved_setups"] == 2
    assert result["setup_win_rate_pct"] == 50.0
    assert result["total_r"] == 0.5
    assert result["derivatives_evidence_status_counts"] == {
        "BEARISH": 1,
        "BULLISH": 1,
        "MISSING": 1,
        "UNKNOWN": 1,
    }
    assert result["historical_memory_available_clicks"] == 2
    assert result["historical_memory_missing_clicks"] == 2
    assert result["options_profitability_evaluated"] is False
