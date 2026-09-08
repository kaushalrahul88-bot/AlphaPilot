from __future__ import annotations

import copy

from app import fno_market_brain_v6 as brain


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


def _long_transition(*, five_volume=1.3, fifteen_volume=1.3, price_action=0):
    return _technical(
        _tf(
            "UPTREND",
            trend=10,
            momentum=6,
            structure_score=8,
            volume=five_volume,
            distance_resistance=0.4,
        ),
        _tf(
            "RANGE",
            momentum=6,
            price_action=price_action,
            volume=fifteen_volume,
            distance_resistance=0.5,
        ),
        _tf("RANGE"),
    )


def _short_transition(*, five_volume=1.3, fifteen_volume=1.3, price_action=0):
    return _technical(
        _tf(
            "DOWNTREND",
            trend=-10,
            momentum=-6,
            structure_score=-8,
            volume=five_volume,
            distance_support=0.4,
        ),
        _tf(
            "RANGE",
            momentum=-6,
            price_action=price_action,
            volume=fifteen_volume,
            distance_support=0.5,
        ),
        _tf("RANGE"),
    )


def test_architecture_is_new_version_and_holdout_a_is_development_only():
    contract = brain.architecture_contract()
    assert contract["holdout_a_outcomes_are_development_only"] is True
    assert contract["holdout_a_reusable_for_v6_promotion"] is False
    assert contract["v5_brain_modified_in_place"] is False
    assert contract["outcomes_read"] is False
    assert contract["option_inputs_read"] is False
    assert contract["futures_inputs_read"] is False
    assert contract["forward_test_run_by_module"] is False
    assert contract["live_execution"] is False


def test_dual_timeframe_volume_confirms_symmetric_transition():
    long_decision = brain.decide("TEST", _long_transition(), _context(relative=0.2))
    short_decision = brain.decide("TEST", _short_transition(), _context(relative=-0.2))

    assert long_decision["action"] == "LONG"
    assert short_decision["action"] == "SHORT"
    assert long_decision["evidence"]["dual_timeframe_volume_expansion"] is True
    assert short_decision["evidence"]["dual_timeframe_volume_expansion"] is True


def test_aligned_15m_price_action_can_confirm_without_dual_volume():
    long_decision = brain.decide(
        "TEST",
        _long_transition(five_volume=1.0, fifteen_volume=1.0, price_action=1),
        _context(relative=0.2),
    )
    short_decision = brain.decide(
        "TEST",
        _short_transition(five_volume=1.0, fifteen_volume=1.0, price_action=-1),
        _context(relative=-0.2),
    )

    assert long_decision["action"] == "LONG"
    assert short_decision["action"] == "SHORT"
    assert long_decision["evidence"]["fifteen_minute_price_action_aligned"] is True
    assert short_decision["evidence"]["fifteen_minute_price_action_aligned"] is True


def test_structure_proximity_alone_no_longer_promotes_transition():
    technical = _long_transition(five_volume=1.0, fifteen_volume=1.0)
    decision = brain.decide("TEST", technical, _context(relative=0.2))

    assert decision["evidence"]["structure_boundary_within_0_75_atr"] is True
    assert decision["evidence"]["structure_boundary_is_diagnostic_only"] is True
    assert decision["action"] == "NO_TRADE"
    assert "TRANSITION_EVIDENCE_NOT_CONFIRMED" in decision["reasons"]


def test_single_timeframe_volume_expansion_is_insufficient():
    five_only = brain.decide(
        "TEST",
        _long_transition(five_volume=1.4, fifteen_volume=1.0),
        _context(relative=0.2),
    )
    fifteen_only = brain.decide(
        "TEST",
        _long_transition(five_volume=1.0, fifteen_volume=1.4),
        _context(relative=0.2),
    )

    assert five_only["action"] == "NO_TRADE"
    assert fifteen_only["action"] == "NO_TRADE"
    assert five_only["evidence"]["dual_timeframe_volume_expansion"] is False
    assert fifteen_only["evidence"]["dual_timeframe_volume_expansion"] is False


def test_relative_strength_is_still_required_for_transition_candidate():
    decision = brain.decide("TEST", _long_transition(), _context(relative=0.0))
    assert decision["action"] == "NO_TRADE"
    assert "NO_TRANSITION_SETUP" in decision["reasons"]


def test_alpha_score_does_not_change_v6_decision():
    low = _long_transition()
    high = copy.deepcopy(low)
    for payload in low["timeframes"].values():
        payload["alpha_score"] = 5
    for payload in high["timeframes"].values():
        payload["alpha_score"] = 95

    low_decision = brain.decide("TEST", low, _context(relative=0.2))
    high_decision = brain.decide("TEST", high, _context(relative=0.2))
    assert low_decision["action"] == high_decision["action"] == "LONG"
    assert low_decision["evidence"] == high_decision["evidence"]


def test_pullback_reentry_is_deliberately_disabled_pending_unseen_evidence():
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
    assert decision["phase"]["phase"] == "MATURE_TREND"
    assert decision["action"] == "NO_TRADE"
    assert "PULLBACK_REENTRY_DISABLED_PENDING_UNSEEN_EVIDENCE" in decision["reasons"]


def test_counter_regime_transition_preserves_v5_reversal_safeguard():
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

    blocked = brain.decide("TEST", technical, _context(relative=0.2, events=-1))
    assert blocked["action"] == "NO_TRADE"
    assert "HIGHER_TIMEFRAME_REGIME_OPPOSED" in blocked["reasons"]


def test_evidence_mode_is_part_of_v6_thesis_identity():
    dual = brain.decide("TEST", _long_transition(), _context(relative=0.2))
    price_action = brain.decide(
        "TEST",
        _long_transition(five_volume=1.0, fifteen_volume=1.0, price_action=1),
        _context(relative=0.2),
    )

    assert dual["action"] == "LONG"
    assert price_action["action"] == "LONG"
    assert dual["thesis"]["evidence_mode"] != price_action["thesis"]["evidence_mode"]
    assert dual["thesis"]["key"] != price_action["thesis"]["key"]


def test_thesis_tracker_still_suppresses_identical_v6_thesis():
    tracker = brain.ThesisTracker()
    decision = brain.decide("TEST", _long_transition(), _context(relative=0.2))
    first = tracker.apply(decision)
    duplicate = tracker.apply(decision)

    assert first["action"] == "LONG"
    assert duplicate["action"] == "NO_TRADE"
    assert duplicate["suppressed_action"] == "LONG"
    assert "DUPLICATE_ACTIVE_THESIS" in duplicate["reasons"]
