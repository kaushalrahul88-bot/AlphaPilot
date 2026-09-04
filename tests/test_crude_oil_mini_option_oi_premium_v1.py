from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.crude_oil_mini_option_oi_premium_v1 import (
    interpret_option_oi_premium,
    registration_contract,
)
from app.crude_oil_mini_participation_v2 import build_participation_observation


IST = timezone(timedelta(hours=5, minutes=30))


def _contract(symbol, option_type, oi_change, premium_change):
    return {
        "trading_symbol": symbol,
        "expiry_date": "2026-09-17",
        "strike": 8600.0,
        "option_type": option_type,
        "oi_change_from_previous_bucket": oi_change,
        "premium_change_from_previous_bucket": premium_change,
    }


def _candles():
    start = datetime(2026, 9, 4, 10, 0, tzinfo=IST)
    rows = []
    for index in range(6):
        price = 8600.0 + index
        rows.append(
            [
                (start + timedelta(minutes=5 * index)).isoformat(),
                price,
                price + 2.0,
                price - 2.0,
                price + 0.5,
                100.0,
                None,
            ]
        )
    return rows


def test_registration_is_threshold_free_prospective_shadow_contract():
    contract = registration_contract()
    assert contract["status"] == "REGISTERED"
    assert contract["model_id"] == "CRUDE_OIL_MINI_OPTION_OI_PREMIUM_INTERPRETATION_V1"
    assert contract["previous_bucket_required"] is True
    assert contract["raw_oi_alone_can_vote"] is False
    assert contract["premium_alone_can_vote"] is False
    assert contract["magnitude_weighting"] is False
    assert contract["performance_fitted_thresholds"] is False
    assert contract["prospective_only"] is True
    assert contract["retroactive_reconstruction_allowed"] is False
    assert contract["shadow_only"] is True
    assert contract["current_mind_effect"] == "NONE"
    assert contract["promotion_allowed"] is False


def test_bullish_requires_call_and_put_side_confirmation():
    result = interpret_option_oi_premium(
        [
            _contract("8600CE", "CE", 100, 5),
            _contract("8650CE", "CE", -50, 3),
            _contract("8600PE", "PE", 200, -4),
            _contract("8650PE", "PE", -80, -2),
        ],
        previous_sample_bucket_at="2026-09-04T10:20:00+05:30",
    )
    assert result["status"] == "COHERENT_TWO_SIDED_CONFIRMATION"
    assert result["direction"] == "BULLISH"
    assert result["counts_for_direction"] is True
    assert result["ce"]["direction"] == "BULLISH"
    assert result["pe"]["direction"] == "BULLISH"


def test_bearish_requires_call_and_put_side_confirmation():
    result = interpret_option_oi_premium(
        [
            _contract("8600CE", "CE", 100, -5),
            _contract("8650CE", "CE", -50, -3),
            _contract("8600PE", "PE", 200, 4),
            _contract("8650PE", "PE", -80, 2),
        ],
        previous_sample_bucket_at="2026-09-04T10:20:00+05:30",
    )
    assert result["status"] == "COHERENT_TWO_SIDED_CONFIRMATION"
    assert result["direction"] == "BEARISH"
    assert result["counts_for_direction"] is True


def test_cross_side_conflict_abstains():
    result = interpret_option_oi_premium(
        [
            _contract("8600CE", "CE", 100, 5),
            _contract("8600PE", "PE", 200, 4),
        ],
        previous_sample_bucket_at="2026-09-04T10:20:00+05:30",
    )
    assert result["status"] == "CROSS_SIDE_CONFLICT"
    assert result["direction"] == "UNKNOWN"
    assert result["counts_for_direction"] is False


def test_missing_previous_bucket_abstains():
    result = interpret_option_oi_premium(
        [_contract("8600CE", "CE", 100, 5)],
        previous_sample_bucket_at=None,
    )
    assert result["status"] == "INSUFFICIENT_PIT_BUCKETS"
    assert result["direction"] == "UNKNOWN"
    assert result["counts_for_direction"] is False


def test_registered_interpretation_can_vote_only_inside_v2_shadow_participation():
    interpretation = interpret_option_oi_premium(
        [
            _contract("8600CE", "CE", 100, 5),
            _contract("8600PE", "PE", 200, -4),
        ],
        previous_sample_bucket_at="2026-09-04T10:20:00+05:30",
    )
    option_positioning = {
        "status": "AVAILABLE",
        "direction": interpretation["direction"],
        "counts_for_direction": interpretation["counts_for_direction"],
        "oi_premium_interpretation": interpretation,
        "futures_oi_required": False,
    }
    result = build_participation_observation(
        _candles(),
        click_timestamp="2026-09-04T10:30:00+05:30",
        snapshot={
            "time_adjusted_relative_volume": 0.5,
            "session_vwap_gap_pct": 0.1,
        },
        profile={"participation_confirming": 2.0},
        option_positioning=option_positioning,
    )
    assert result["state"] == "OPTION_OI_PREMIUM_CAUSAL_RULE_V1"
    assert result["causal_origin"] == "OPTION_OI_PREMIUM_FLOW"
    assert result["stance"] == "BULLISH"
    assert result["counts_for_direction"] is True
    assert result["independence_status"] == "INDEPENDENT"
    assert result["detail"]["registered_option_model_vote"] is True
