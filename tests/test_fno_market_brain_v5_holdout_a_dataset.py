from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app import fno_market_brain_v5_holdout_a_dataset as dataset
from app.fno_market_brain_v3_current_expiry_dataset import events_at

IST = ZoneInfo("Asia/Kolkata")


def test_holdout_a_universe_windows_and_frozen_brain():
    assert dataset.STOCKS == ("HDFCBANK", "INFY", "MARUTI", "BHARTIARTL")
    assert dataset.WINDOWS == (
        ("JUNE_2026", date(2026, 6, 1), date(2026, 6, 12)),
        ("JULY_2026", date(2026, 7, 1), date(2026, 7, 14)),
    )
    assert dataset.BRAIN_FROZEN_COMMIT == "a5a39253c1aa79f6663cd11b41f7d6607b96af90"


def test_clicks_are_deterministic_unique_and_within_frozen_pool():
    day = date(2026, 6, 4)
    first = dataset.deterministic_clicks(day)
    second = dataset.deterministic_clicks(day)
    assert first == second
    assert len(first) == dataset.CLICKS_PER_DAY == 20
    assert len(set(first)) == 20
    local = [stamp.astimezone(IST) for stamp in first]
    assert min(stamp.time() for stamp in local).isoformat() >= "09:30:00"
    assert max(stamp.time() for stamp in local).isoformat() <= "14:00:00"


def test_clicks_outside_holdout_are_rejected():
    try:
        dataset.deterministic_clicks(date(2026, 8, 31))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a date outside frozen Holdout A windows")


def test_july_inflation_event_is_point_in_time_only():
    knowledge = dataset.load_knowledge()
    events = list(knowledge["events"])
    before = events_at(
        "HDFCBANK",
        "PRIVATE_BANKING",
        datetime(2026, 7, 13, 14, 0, tzinfo=IST),
        events,
    )
    after = events_at(
        "HDFCBANK",
        "PRIVATE_BANKING",
        datetime(2026, 7, 14, 9, 30, tzinfo=IST),
        events,
    )
    before_ids = {item["id"] for item in before}
    after_ids = {item["id"] for item in after}
    assert "INDIA_CPI_2026_07_13" not in before_ids
    assert "INDIA_CPI_2026_07_13" in after_ids


def test_holdout_contract_blocks_forward_and_outcome_evaluation():
    safety = dataset.architecture_contract()
    assert safety["historical_backtest_dataset_only"] is True
    assert safety["forward_test"] is False
    assert safety["future_outcomes_resolved"] is False
    assert safety["evaluation_metrics_computed"] is False
    assert safety["options_read_for_decision"] is False
    assert safety["futures_read_for_decision"] is False
    assert safety["v3_development_rows_used_as_holdout"] is False
    assert safety["live_execution"] is False
    assert safety["capital_committed"] == 0


def test_knowledge_archive_was_frozen_without_outcomes():
    knowledge = dataset.load_knowledge()
    policy = knowledge["policy"]
    assert policy["outcomes_used_to_build_archive"] is False
    assert policy["future_bars_used_for_decision"] is False
    assert policy["options_used_for_decision"] is False
    assert policy["futures_used_for_decision"] is False
