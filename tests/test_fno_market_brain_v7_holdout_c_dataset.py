from __future__ import annotations

from datetime import date, datetime, time

from app import fno_market_brain_v7 as brain
from app import fno_market_brain_v7_holdout_c_dataset as dataset
from app.providers.groww import GrowwProvider


PRIOR_TARGETS = {
    "ONGC", "LTIM", "SBIN", "SUNPHARMA",
    "HDFCBANK", "INFY", "MARUTI", "BHARTIARTL",
    "RELIANCE", "TATASTEEL", "ITC", "BAJFINANCE", "LT", "DRREDDY",
    "ULTRACEMCO", "POWERGRID",
}


def test_protocol_brain_windows_and_targets_are_frozen_before_outcomes():
    assert dataset.PROTOCOL_ID == "FNO_MARKET_BRAIN_V7_HOLDOUT_C_2026-09-08"
    assert dataset.BRAIN_FROZEN_COMMIT == "461053bb98ef60ffdad9f9bd34ce4a5e599016c3"
    assert brain.PROTOCOL_ID == "FNO_MARKET_BRAIN_V7_PERSISTENCE_CONFIRMATION_2026-09-08"
    assert dataset.WINDOWS == (
        ("JANUARY_2026", date(2026, 1, 5), date(2026, 1, 30)),
        ("FEBRUARY_2026", date(2026, 2, 2), date(2026, 2, 27)),
        ("MARCH_2026", date(2026, 3, 2), date(2026, 3, 27)),
    )
    assert dataset.STOCKS == (
        "ICICIBANK", "TCS", "M&M", "HINDUNILVR", "CIPLA", "HINDALCO",
        "NTPC", "TITAN", "ASIANPAINT", "ADANIPORTS", "COALINDIA", "BRITANNIA",
    )
    assert set(dataset.STOCKS).isdisjoint(PRIOR_TARGETS)


def test_target_and_peer_symbols_are_supported_by_groww_cash_mapping():
    knowledge = dataset.load_knowledge()
    supported = set(GrowwProvider.NSE_CASH_SYMBOLS)
    assert set(dataset.STOCKS) <= supported
    for symbol in dataset.STOCKS:
        peers = set(knowledge["stocks"][symbol]["peer_basket"])
        assert peers
        assert peers <= supported


def test_click_schedule_is_deterministic_unique_common_pool():
    day = date(2026, 2, 10)
    first = dataset.deterministic_clicks(day)
    second = dataset.deterministic_clicks(day)
    assert first == second
    assert len(first) == dataset.CLICKS_PER_DAY == 20
    assert len(set(first)) == 20
    assert first == sorted(first)
    local_times = [item.astimezone(dataset.IST).time() for item in first]
    assert all(time(9, 30) <= value <= time(14, 0) for value in local_times)
    assert all(value.minute % 5 == 0 for value in local_times)


def test_click_schedule_rejects_outside_holdout():
    try:
        dataset.deterministic_clicks(date(2026, 4, 1))
    except ValueError as exc:
        assert "outside frozen Holdout C windows" in str(exc)
    else:
        raise AssertionError("outside-window click generation must fail")


def test_knowledge_matches_targets_and_is_outcome_blind():
    knowledge = dataset.load_knowledge()
    assert tuple(knowledge["stocks"].keys()) == dataset.STOCKS
    policy = knowledge["policy"]
    assert policy["v3_development_rows_excluded"] is True
    assert policy["v5_holdout_a_rows_excluded"] is True
    assert policy["v6_holdout_b_rows_excluded"] is True
    assert policy["outcomes_used_to_build_archive"] is False
    assert policy["future_bars_used_for_decision"] is False
    assert policy["options_used_for_decision"] is False
    assert policy["futures_used_for_decision"] is False
    assert policy["unknown_intraday_event_time"] == "NEXT_NSE_SESSION_OPEN"
    for symbol in dataset.STOCKS:
        profile = knowledge["stocks"][symbol]
        assert profile["peer_basket"]
        assert profile["category"]
        assert profile["business"]
        assert profile["primary_drivers"]


def test_event_timestamps_are_aware_and_labels_are_conservative():
    knowledge = dataset.load_knowledge()
    assert knowledge["events"]
    for event in knowledge["events"]:
        parsed = datetime.fromisoformat(event["effective_at"])
        assert parsed.tzinfo is not None
        assert event["direction"] in {"POSITIVE", "NEGATIVE", "NEUTRAL"}
        assert int(event["weight"]) in {0, 1}
        assert event["source"].startswith("https://")
        assert event["notes"]
        if event["direction"] == "NEUTRAL":
            assert int(event["weight"]) == 0


def test_architecture_contract_blocks_reused_holdouts_forward_and_derivatives():
    contract = dataset.architecture_contract()
    assert contract["historical_backtest_dataset_only"] is True
    assert contract["forward_test"] is False
    assert contract["future_outcomes_resolved"] is False
    assert contract["evaluation_metrics_computed"] is False
    assert contract["options_read_for_decision"] is False
    assert contract["futures_read_for_decision"] is False
    assert contract["live_execution"] is False
    assert contract["capital_committed"] == 0
    assert contract["v3_development_rows_used_as_holdout"] is False
    assert contract["v5_holdout_a_rows_used_as_holdout"] is False
    assert contract["v6_holdout_b_rows_used_as_holdout"] is False
    assert contract["history_cache_may_change_decisions"] is False
    assert contract["bounded_fetch_concurrency"] == 4


def test_session_filter_keeps_only_frozen_calendar_windows():
    rows = [
        [datetime(2026, 1, 2, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 1, 5, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 1, 30, 15, 25, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 2, 2, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 2, 27, 15, 25, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 3, 2, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 3, 27, 15, 25, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 3, 30, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
    ]
    assert dataset.sessions_from_tape(rows) == [
        date(2026, 1, 5), date(2026, 1, 30),
        date(2026, 2, 2), date(2026, 2, 27),
        date(2026, 3, 2), date(2026, 3, 27),
    ]
