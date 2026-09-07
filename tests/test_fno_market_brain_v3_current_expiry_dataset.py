import asyncio
from datetime import date, datetime, timedelta

from app import fno_market_brain_v3_current_expiry_dataset as dataset


def test_four_new_stocks_are_frozen_and_distinct_categories():
    assert dataset.STOCKS == ("HDFCBANK", "INFY", "MARUTI", "BHARTIARTL")
    categories = [category for _, category in dataset.FROZEN_STOCKS]
    assert len(categories) == len(set(categories)) == 4


def test_current_expiry_window_is_frozen():
    assert dataset.EXPIRY_START == date(2026, 8, 26)
    assert dataset.EXPIRY_DATE == date(2026, 9, 29)
    assert dataset.DATA_END == date(2026, 9, 7)


def test_random_clicks_are_deterministic_and_twenty_per_session():
    day = date(2026, 9, 1)
    first = dataset.deterministic_clicks(day)
    second = dataset.deterministic_clicks(day)
    assert first == second
    assert len(first) == 20
    assert len(set(first)) == 20
    assert all(click.date() == day for click in [value.astimezone(dataset.IST) for value in first])


def test_history_windows_are_contiguous_and_preserve_exact_range():
    start = datetime.fromisoformat("2026-07-02T09:15:00+05:30")
    end = datetime.fromisoformat("2026-07-14T15:30:00+05:30")
    windows = dataset._history_windows("5m", start, end)
    assert len(windows) >= 2
    assert windows[0][0] == start
    assert windows[-1][1] == end
    step = timedelta(minutes=5)
    for left, right in zip(windows, windows[1:]):
        assert left[1] + step == right[0]


def test_segmented_fetch_retries_only_failed_window(monkeypatch):
    start = datetime.fromisoformat("2026-07-02T09:15:00+05:30")
    end = datetime.fromisoformat("2026-07-08T15:30:00+05:30")
    windows = dataset._history_windows("5m", start, end)
    calls = {}

    async def fake_chunk(provider, symbol, timeframe, window_start, window_end):
        key = (window_start, window_end)
        calls[key] = calls.get(key, 0) + 1
        if key == windows[0] and calls[key] == 1:
            raise TimeoutError("transient")
        return [[window_start.isoformat(), 1, 1, 1, 1, 1]]

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(dataset.baseline, "_chunk", fake_chunk)
    monkeypatch.setattr(dataset.baseline, "_merge", lambda rows: rows)
    monkeypatch.setattr(dataset.asyncio, "sleep", no_sleep)

    rows, failures = asyncio.run(
        dataset._fetch(object(), "INFY", "5m", start, end)
    )
    assert len(rows) == len(windows)
    assert calls[windows[0]] == 2
    assert all(calls[window] == 1 for window in windows[1:])
    assert any("TimeoutError" in failure for failure in failures)


def test_point_in_time_event_policy_excludes_future_event_and_keeps_known_macro():
    knowledge = dataset.load_knowledge()
    click = datetime.fromisoformat("2026-09-07T13:00:00+05:30")
    events = dataset.events_at("INFY", "INFORMATION_TECHNOLOGY", click, knowledge["events"])
    ids = {event["id"] for event in events}
    assert "IT_US_RATE_PRESSURE_2026_09_07" not in ids
    assert "IT_US_JOBS_HAWKISH_2026_09_04" in ids
    assert "MARKET_HORMUZ_WEEKEND_ESCALATION_2026_09_06" in ids
    assert "INFY_SUBSIDIARY_LIQUIDATIONS_2026_09_03" in ids


def test_intraday_maruti_news_is_not_visible_before_its_timestamp():
    knowledge = dataset.load_knowledge()
    before = datetime.fromisoformat("2026-09-07T11:35:00+05:30")
    after = datetime.fromisoformat("2026-09-07T11:45:00+05:30")
    before_ids = {
        event["id"]
        for event in dataset.events_at("MARUTI", "PASSENGER_AUTOMOBILES", before, knowledge["events"])
    }
    after_ids = {
        event["id"]
        for event in dataset.events_at("MARUTI", "PASSENGER_AUTOMOBILES", after, knowledge["events"])
    }
    assert "MARUTI_PRICE_HIKE_2026_09_07" not in before_ids
    assert "MARUTI_PRICE_HIKE_2026_09_07" in after_ids


def test_research_contract_freezes_future_tape_without_evaluation():
    contract = dataset.architecture_contract()
    assert contract["brain_protocol_id"] == "FNO_MARKET_BRAIN_V3_2026-09-07"
    assert contract["future_tape_frozen"] is True
    assert contract["future_outcomes_resolved"] is False
    assert contract["evaluation_metrics_computed"] is False
    assert contract["options_read_for_decision"] is False
    assert contract["futures_read_for_decision"] is False
    assert contract["v3_thresholds_changed"] is False
    assert contract["history_fetch_recovery"] == "SEGMENTED_RETRY_SAME_FROZEN_RANGE"
