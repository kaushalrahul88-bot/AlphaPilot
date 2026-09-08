from __future__ import annotations

import copy

from app import fno_market_brain_v6 as v6
from app import fno_market_brain_v7 as brain


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


def _long_transition(
    *,
    five_volume=1.3,
    fifteen_volume=1.3,
    fifteen_price_action=0,
    hour_price_action=0,
    near=True,
):
    distance = 0.4 if near else 2.0
    return _technical(
        _tf(
            "UPTREND",
            trend=10,
            momentum=6,
            structure_score=8,
            volume=five_volume,
            distance_resistance=distance,
        ),
        _tf(
            "RANGE",
            momentum=6,
            price_action=fifteen_price_action,
            volume=fifteen_volume,
            distance_resistance=distance,
        ),
        _tf("RANGE", price_action=hour_price_action),
    )


def _short_transition(
    *,
    five_volume=1.3,
    fifteen_volume=1.3,
    fifteen_price_action=0,
    hour_price_action=0,
    near=True,
):
    distance = 0.4 if near else 2.0
    return _technical(
        _tf(
            "DOWNTREND",
            trend=-10,
            momentum=-6,
            structure_score=-8,
            volume=five_volume,
            distance_support=distance,
        ),
        _tf(
            "RANGE",
            momentum=-6,
            price_action=fifteen_price_action,
            volume=fifteen_volume,
            distance_support=distance,
        ),
        _tf("RANGE", price_action=hour_price_action),
    )


def test_v7_is_new_version_and_holdout_b_is_development_only():
    contract = brain.architecture_contract()
    assert contract["v6_holdout_b_outcomes_are_development_only"] is True
    assert contract["v6_holdout_b_reusable_for_v7_promotion"] is False
    assert contract["v6_brain_modified_in_place"] is False
    assert contract["v7_can_create_trade_rejected_by_v6"] is False
    assert contract["new_numeric_threshold_fit_to_holdout_b"] is False
    assert contract["outcomes_read"] is False
    assert contract["option_inputs_read"] is False
    assert contract["futures_inputs_read"] is False
    assert contract["forward_test_run_by_module"] is False


def test_aligned_1h_price_action_confirms_v6_transition_symmetrically():
    long_technical = _long_transition(hour_price_action=1)
    short_technical = _short_transition(hour_price_action=-1)

    long_base = v6.decide("TEST", long_technical, _context(relative=0.2))
    short_base = v6.decide("TEST", short_technical, _context(relative=-0.2))
    assert long_base["action"] == "LONG"
    assert short_base["action"] == "SHORT"

    long_decision = brain.decide("TEST", long_technical, _context(relative=0.2))
    short_decision = brain.decide("TEST", short_technical, _context(relative=-0.2))
    assert long_decision["action"] == "LONG"
    assert short_decision["action"] == "SHORT"
    assert long_decision["persistence"]["route"] == "ALIGNED_1H_PRICE_ACTION"
    assert short_decision["persistence"]["route"] == "ALIGNED_1H_PRICE_ACTION"


def test_opposed_1h_price_action_blocks_even_strong_local_confluence():
    technical = _long_transition(
        fifteen_price_action=1,
        hour_price_action=-1,
        near=True,
    )
    base = v6.decide("TEST", technical, _context(relative=0.2))
    decision = brain.decide("TEST", technical, _context(relative=0.2))

    assert base["action"] == "LONG"
    assert decision["persistence"]["local_confluence"] is True
    assert decision["persistence"]["higher_timeframe_price_action_opposed"] is True
    assert decision["action"] == "NO_TRADE"
    assert "V7_HIGHER_TIMEFRAME_PRICE_ACTION_OPPOSED" in decision["reasons"]


def test_neutral_1h_price_action_requires_all_local_confluence_components():
    confirmed = brain.decide(
        "TEST",
        _long_transition(fifteen_price_action=1, hour_price_action=0, near=True),
        _context(relative=0.2),
    )
    no_price_action = brain.decide(
        "TEST",
        _long_transition(fifteen_price_action=0, hour_price_action=0, near=True),
        _context(relative=0.2),
    )
    no_dual_volume = brain.decide(
        "TEST",
        _long_transition(
            five_volume=1.0,
            fifteen_volume=1.0,
            fifteen_price_action=1,
            hour_price_action=0,
            near=True,
        ),
        _context(relative=0.2),
    )
    no_structure = brain.decide(
        "TEST",
        _long_transition(fifteen_price_action=1, hour_price_action=0, near=False),
        _context(relative=0.2),
    )

    assert confirmed["action"] == "LONG"
    assert confirmed["persistence"]["route"] == "NEUTRAL_1H_WITH_LOCAL_CONFLUENCE"
    assert no_price_action["base_action"] == "LONG"
    assert no_price_action["action"] == "NO_TRADE"
    assert no_dual_volume["base_action"] == "LONG"
    assert no_dual_volume["action"] == "NO_TRADE"
    assert no_structure["base_action"] == "LONG"
    assert no_structure["action"] == "NO_TRADE"


def test_v7_never_promotes_a_v6_no_trade():
    technical = _long_transition(hour_price_action=1)
    base = v6.decide("TEST", technical, _context(relative=0.0))
    decision = brain.decide("TEST", technical, _context(relative=0.0))

    assert base["action"] == "NO_TRADE"
    assert decision["base_action"] == "NO_TRADE"
    assert decision["action"] == "NO_TRADE"
    assert decision["persistence"]["reason"] == "V6_BASE_NOT_ACTIONABLE"


def test_alpha_score_does_not_change_v7_decision():
    low = _long_transition(hour_price_action=1)
    high = copy.deepcopy(low)
    for payload in low["timeframes"].values():
        payload["alpha_score"] = 5
    for payload in high["timeframes"].values():
        payload["alpha_score"] = 95

    low_decision = brain.decide("TEST", low, _context(relative=0.2))
    high_decision = brain.decide("TEST", high, _context(relative=0.2))
    assert low_decision["action"] == high_decision["action"] == "LONG"
    assert low_decision["persistence"] == high_decision["persistence"]


def test_persistence_route_is_part_of_v7_thesis_identity():
    aligned = brain.decide(
        "TEST",
        _long_transition(hour_price_action=1),
        _context(relative=0.2),
    )
    local = brain.decide(
        "TEST",
        _long_transition(fifteen_price_action=1, hour_price_action=0),
        _context(relative=0.2),
    )

    assert aligned["action"] == local["action"] == "LONG"
    assert aligned["thesis"]["persistence_route"] != local["thesis"]["persistence_route"]
    assert aligned["thesis"]["key"] != local["thesis"]["key"]


def test_thesis_tracker_suppresses_identical_v7_thesis():
    tracker = brain.ThesisTracker()
    decision = brain.decide(
        "TEST",
        _long_transition(hour_price_action=1),
        _context(relative=0.2),
    )
    first = tracker.apply(decision)
    duplicate = tracker.apply(decision)

    assert first["action"] == "LONG"
    assert duplicate["action"] == "NO_TRADE"
    assert duplicate["suppressed_action"] == "LONG"
    assert "DUPLICATE_ACTIVE_THESIS" in duplicate["reasons"]
