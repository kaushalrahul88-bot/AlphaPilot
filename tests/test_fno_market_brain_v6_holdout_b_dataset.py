from __future__ import annotations

from datetime import date, datetime, time

from app import fno_market_brain_v6 as brain
from app import fno_market_brain_v6_holdout_b_dataset as dataset


def test_protocol_and_brain_are_frozen_before_outcomes():
    assert dataset.PROTOCOL_ID == "FNO_MARKET_BRAIN_V6_HOLDOUT_B_2026-09-08"
    assert dataset.BRAIN_FROZEN_COMMIT == "09fe4ca938b4b47c5d9cb8d33b91f321d5368ef6"
    assert brain.PROTOCOL_ID == "FNO_MARKET_BRAIN_V6_EVIDENCE_TRANSITION_2026-09-08"
    assert dataset.WINDOWS == (
        ("APRIL_2026", date(2026, 4, 1), date(2026, 4, 24)),
        ("MAY_2026", date(2026, 5, 4), date(2026, 5, 22)),
    )
    assert dataset.STOCKS == (
        "RELIANCE",
        "TATASTEEL",
        "ITC",
        "BAJFINANCE",
        "LT",
        "DRREDDY",
        "ULTRACEMCO",
        "POWERGRID",
    )


def test_click_schedule_is_deterministic_unique_and_in_frozen_pool():
    day = date(2026, 4, 6)
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
        dataset.deterministic_clicks(date(2026, 6, 1))
    except ValueError as exc:
        assert "outside frozen Holdout B windows" in str(exc)
    else:
        raise AssertionError("outside-window click generation must fail")


def test_knowledge_matches_frozen_stocks_and_is_outcome_blind():
    knowledge = dataset.load_knowledge()
    assert set(knowledge["stocks"]) == set(dataset.STOCKS)
    policy = knowledge["policy"]
    assert policy["v3_development_rows_excluded"] is True
    assert policy["v5_holdout_a_outcomes_excluded"] is True
    assert policy["outcomes_used_to_build_archive"] is False
    assert policy["future_bars_used_for_decision"] is False
    assert policy["options_used_for_decision"] is False
    assert policy["futures_used_for_decision"] is False
    assert policy["unknown_intraday_event_time"] == "NEXT_NSE_SESSION_OPEN"
    for symbol in dataset.STOCKS:
        profile = knowledge["stocks"][symbol]
        assert profile["peer_basket"]
        assert profile["category"]


def test_event_timestamps_are_timezone_aware_and_direction_is_intrinsic():
    knowledge = dataset.load_knowledge()
    for event in knowledge["events"]:
        parsed = datetime.fromisoformat(event["effective_at"])
        assert parsed.tzinfo is not None
        assert event["direction"] in {"POSITIVE", "NEGATIVE", "NEUTRAL"}
        assert int(event["weight"]) in {0, 1}
        assert event["source"].startswith("https://")
        if event["direction"] == "NEUTRAL":
            assert int(event["weight"]) == 0


def test_architecture_contract_blocks_forward_and_derivatives():
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
    assert contract["history_cache_may_change_decisions"] is False
    assert contract["bounded_fetch_concurrency"] == 4


def test_session_filter_keeps_only_frozen_windows():
    rows = [
        [datetime(2026, 3, 31, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 4, 1, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 4, 24, 15, 25, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 4, 27, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 5, 4, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 5, 22, 15, 25, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
        [datetime(2026, 5, 25, 9, 15, tzinfo=dataset.IST).timestamp(), 1, 2, 0, 1],
    ]
    assert dataset.sessions_from_tape(rows) == [
        date(2026, 4, 1),
        date(2026, 4, 24),
        date(2026, 5, 4),
        date(2026, 5, 22),
    ]
