from __future__ import annotations

import copy

from app import fno_market_brain_v5 as brain


def _tf(
    structure="RANGE",
    *,
    trend=0,
    momentum=0,
    structure_score=0,
    price_action=0,
    volume=1.0,
    distance_resistance=2.0,
    distance_support=2.0,
    alpha=50,
):
    return {
        "market_structure": structure,
        "family_scores": {
            "trend": trend,
            "momentum": momentum,
            "structure": structure_score,
            "volume": 0,
            "volatility": 0,
            "price_action": price_action,
        },
        "volume_ratio_capped": volume,
        "volume_ratio_raw": volume,
        "distance_to_resistance_atr": distance_resistance,
        "distance_to_support_atr": distance_support,
        "alpha_score": alpha,
    }


def _technical(five, fifteen, hour):
    return {"timeframes": {"5m": five, "15m": fifteen, "1h": hour}}


def _context(relative=0.0, events=0):
    return {
        "relative_strength_vs_nifty_pct": relative,
        "components": {"events": events},
    }


def _long_transition():
    return _technical(
        _tf(
            "UPTREND",
            trend=10,
            momentum=6,
            structure_score=8,
            volume=1.0,
            distance_resistance=0.4,
        ),
        _tf(
            "RANGE",
            momentum=6,
            volume=1.3,
            distance_resistance=0.5,
        ),
        _tf("RANGE"),
    )


def _short_transition():
    return _technical(
        _tf(
            "DOWNTREND",
            trend=-10,
            momentum=-6,
            structure_score=-8,
            volume=1.0,
            distance_support=0.4,
        ),
        _tf(
            "RANGE",
            momentum=-6,
            volume=1.3,
            distance_support=0.5,
        ),
        _tf("RANGE"),
    )


def test_architecture_is_outcome_blind_and_derivative_free():
    contract = brain.architecture_contract()
    assert contract["outcomes_read"] is False
    assert contract["future_bars_read"] is False
    assert contract["option_inputs_read"] is False
    assert contract["futures_inputs_read"] is False
    assert contract["forward_test_run_by_module"] is False
    assert contract["live_execution"] is False
    assert contract["v3_720_rows_are_development_only"] is True
    assert contract["v3_discovered_short_pattern_declared_proven"] is False


def test_alpha_score_is_not_a_direction_or_expansion_gate():
    low = _long_transition()
    high = copy.deepcopy(low)
    for payload in low["timeframes"].values():
        payload["alpha_score"] = 5
    for payload in high["timeframes"].values():
        payload["alpha_score"] = 95

    low_decision = brain.decide("TEST", low, _context(relative=0.2))
    high_decision = brain.decide("TEST", high, _context(relative=0.2))
    assert low_decision["action"] == "LONG"
    assert high_decision["action"] == "LONG"
    assert low_decision["phase"] == high_decision["phase"]
    assert low_decision["expansion"] == high_decision["expansion"]


def test_transition_rules_are_symmetric_for_long_and_short():
    long_decision = brain.decide("TEST", _long_transition(), _context(relative=0.2))
    short_decision = brain.decide("TEST", _short_transition(), _context(relative=-0.2))

    assert long_decision["action"] == "LONG"
    assert short_decision["action"] == "SHORT"
    assert long_decision["setup_type"] == "TRANSITION"
    assert short_decision["setup_type"] == "TRANSITION"


def test_transition_needs_relative_strength_and_selective_expansion_confirmation():
    technical = _long_transition()

    no_relative = brain.decide("TEST", technical, _context(relative=0.0))
    assert no_relative["action"] == "NO_TRADE"
    assert "NO_TRANSITION_OR_REENTRY_SETUP" in no_relative["reasons"]

    weak_expansion = copy.deepcopy(technical)
    weak_expansion["timeframes"]["5m"]["volume_ratio_capped"] = 1.0
    weak_expansion["timeframes"]["15m"]["volume_ratio_capped"] = 1.0
    weak_expansion["timeframes"]["5m"]["distance_to_resistance_atr"] = 2.0
    weak_expansion["timeframes"]["15m"]["distance_to_resistance_atr"] = 2.0
    decision = brain.decide("TEST", weak_expansion, _context(relative=0.2))
    assert decision["action"] == "NO_TRADE"
    assert "TRANSITION_NOT_CONFIRMED" in decision["reasons"]


def test_higher_timeframe_opposition_blocks_unconfirmed_transition():
    technical = _long_transition()
    technical["timeframes"]["1h"] = _tf(
        "DOWNTREND",
        trend=-12,
        momentum=-6,
        structure_score=-12,
    )

    decision = brain.decide("TEST", technical, _context(relative=0.2))
    assert decision["action"] == "NO_TRADE"
    assert "HIGHER_TIMEFRAME_REGIME_OPPOSED" in decision["reasons"]


def test_counter_regime_transition_requires_explicit_reversal_and_no_news_conflict():
    technical = _long_transition()
    technical["timeframes"]["15m"]["family_scores"]["trend"] = 10
    technical["timeframes"]["1h"] = _tf(
        "DOWNTREND",
        trend=-12,
        momentum=-6,
        structure_score=-12,
    )

    allowed = brain.decide("TEST", technical, _context(relative=0.2, events=0))
    assert allowed["action"] == "LONG"
    assert allowed["counter_regime"] is True
    assert allowed["counter_regime_reversal"]["confirmed"] is True

    opposed_by_news = brain.decide("TEST", technical, _context(relative=0.2, events=-1))
    assert opposed_by_news["action"] == "NO_TRADE"
    assert "HIGHER_TIMEFRAME_REGIME_OPPOSED" in opposed_by_news["reasons"]
    assert opposed_by_news["counter_regime_reversal"]["news_conflict"] is True


def test_mature_trend_is_not_chased_without_pullback_reentry():
    technical = _technical(
        _tf(
            "UPTREND",
            trend=10,
            momentum=6,
            structure_score=8,
            volume=1.2,
            distance_support=2.0,
        ),
        _tf("UPTREND", trend=10, momentum=6, structure_score=8, volume=1.2),
        _tf("UPTREND", trend=12, momentum=6, structure_score=10),
    )

    decision = brain.decide("TEST", technical, _context(relative=0.2))
    assert decision["phase"]["phase"] == "MATURE_TREND"
    assert decision["action"] == "NO_TRADE"
    assert "MATURE_TREND_CHASE_BLOCKED" in decision["reasons"]


def test_mature_trend_can_reenter_after_symmetric_pullback_recovery():
    technical = _technical(
        _tf(
            "RANGE",
            trend=10,
            momentum=6,
            volume=0.9,
            distance_support=0.4,
        ),
        _tf("UPTREND", trend=10, momentum=6, structure_score=8, volume=1.0),
        _tf("UPTREND", trend=12, momentum=6, structure_score=10),
    )

    decision = brain.decide("TEST", technical, _context(relative=0.2))
    assert decision["action"] == "LONG"
    assert decision["setup_type"] == "PULLBACK_REENTRY"
    assert decision["expansion"]["pullback_location_within_0_75_atr"] is True


def test_exhaustion_is_observed_but_not_traded_as_reversal():
    technical = _technical(
        _tf("RANGE", trend=0, momentum=-6),
        _tf("UPTREND", trend=10, momentum=-6, structure_score=8),
        _tf("UPTREND", trend=12, momentum=6, structure_score=10),
    )
    decision = brain.decide("TEST", technical, _context(relative=-0.2))
    assert decision["phase"]["phase"] == "EXHAUSTION"
    assert decision["action"] == "NO_TRADE"
    assert "EXHAUSTION_REQUIRES_NEW_STRUCTURE" in decision["reasons"]


def test_thesis_tracker_suppresses_repeat_until_state_changes_or_reset():
    tracker = brain.ThesisTracker()
    decision = brain.decide("TEST", _long_transition(), _context(relative=0.2))

    first = tracker.apply(decision)
    duplicate = tracker.apply(decision)
    assert first["action"] == "LONG"
    assert duplicate["action"] == "NO_TRADE"
    assert duplicate["suppressed_action"] == "LONG"
    assert "DUPLICATE_ACTIVE_THESIS" in duplicate["reasons"]

    tracker.reset("TEST")
    after_reset = tracker.apply(decision)
    assert after_reset["action"] == "LONG"


def test_missing_context_fails_safe_without_transition_promotion():
    decision = brain.decide("TEST", _long_transition(), {})
    assert decision["action"] == "NO_TRADE"
    assert decision["context_diagnostics"]["relative_strength_side"] is None
