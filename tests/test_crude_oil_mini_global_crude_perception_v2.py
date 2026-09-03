from datetime import datetime, timedelta, timezone

from app.crude_oil_mini_global_crude_perception_v2 import (
    architecture_contract,
    build_global_crude_perception,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _feed(direction: int = 1, *, future_flip: bool = False):
    start = datetime(2026, 9, 3, 9, 0, tzinfo=IST)
    rows = []
    price = 100.0
    for index in range(14):
        open_ = price
        close = open_ + direction * 1.0
        high = max(open_, close) + 0.25
        low = min(open_, close) - 0.25
        bar_start = start + timedelta(hours=index)
        rows.append({
            "bar_start": bar_start.isoformat(),
            "available_at": (bar_start + timedelta(hours=1)).isoformat(),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000 + index * 10,
        })
        price = close
    if future_flip:
        bar_start = start + timedelta(hours=20)
        rows.append({
            "bar_start": bar_start.isoformat(),
            "available_at": (bar_start + timedelta(hours=1)).isoformat(),
            "open": price,
            "high": price + 0.25,
            "low": price - 10.25,
            "close": price - 10.0,
            "volume": 999999,
        })
    return {
        "status": "AVAILABLE",
        "source": "TEST",
        "bar_minutes": 60,
        "data": rows,
    }


def test_both_benchmarks_must_show_structure_and_multi_hour_confirmation():
    probe = {"feeds": {"WTI_CRUDE": _feed(1), "BRENT_CRUDE": _feed(1)}}
    result = build_global_crude_perception(probe, "2026-09-03T23:30:00+05:30")
    assert result["stance"] == "BULLISH"
    assert result["counts_for_direction"] is True
    assert result["state"] == "WTI_BRENT_STRUCTURE_MOMENTUM_CONFIRMED"
    assert result["benchmarks"]["WTI_CRUDE"]["structure"] == "UPTREND"
    assert result["benchmarks"]["BRENT_CRUDE"]["structure"] == "UPTREND"


def test_wti_brent_disagreement_does_not_create_global_vote():
    probe = {"feeds": {"WTI_CRUDE": _feed(1), "BRENT_CRUDE": _feed(-1)}}
    result = build_global_crude_perception(probe, "2026-09-03T23:30:00+05:30")
    assert result["stance"] == "UNKNOWN"
    assert result["counts_for_direction"] is False
    assert result["state"] == "WTI_BRENT_DIRECTION_CONFLICT"


def test_future_hourly_bar_cannot_change_click_state():
    click = "2026-09-03T23:30:00+05:30"
    base = {"feeds": {"WTI_CRUDE": _feed(1), "BRENT_CRUDE": _feed(1)}}
    with_future = {"feeds": {"WTI_CRUDE": _feed(1, future_flip=True), "BRENT_CRUDE": _feed(1, future_flip=True)}}
    first = build_global_crude_perception(base, click)
    second = build_global_crude_perception(with_future, click)
    assert first["stance"] == second["stance"] == "BULLISH"
    assert first["benchmarks"]["WTI_CRUDE"]["latest_close"] == second["benchmarks"]["WTI_CRUDE"]["latest_close"]


def test_single_benchmark_is_context_only_not_a_vote():
    probe = {"feeds": {"WTI_CRUDE": _feed(1)}}
    result = build_global_crude_perception(probe, "2026-09-03T23:30:00+05:30")
    assert result["stance"] == "UNKNOWN"
    assert result["counts_for_direction"] is False
    assert result["state"] == "PARTIAL_BENCHMARK_CONFIRMATION_ONLY"


def test_contract_keeps_component_shadow_and_forbids_august_threshold_search():
    contract = architecture_contract()
    assert contract["research_only"] is True
    assert contract["current_mind_effect"] == "NONE"
    assert contract["direction_v2_effect_until_explicit_wiring"] == "NONE"
    assert contract["single_hour_sign_vote_allowed"] is False
    assert contract["threshold_search_on_inspected_august_allowed"] is False
    assert contract["promotion_allowed"] is False
