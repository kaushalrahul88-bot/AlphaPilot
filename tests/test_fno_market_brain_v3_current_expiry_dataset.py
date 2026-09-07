from datetime import date, datetime

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


def test_point_in_time_event_policy_excludes_future_event():
    knowledge = dataset.load_knowledge()
    click = datetime.fromisoformat("2026-09-07T13:00:00+05:30")
    events = dataset.events_at("INFY", "INFORMATION_TECHNOLOGY", click, knowledge["events"])
    ids = {event["id"] for event in events}
    assert "IT_US_RATE_PRESSURE_2026_09_07" not in ids
    assert "INFY_SUBSIDIARY_LIQUIDATIONS_2026_09_03" in ids


def test_research_contract_freezes_future_tape_without_evaluation():
    contract = dataset.architecture_contract()
    assert contract["brain_protocol_id"] == "FNO_MARKET_BRAIN_V3_2026-09-07"
    assert contract["future_tape_frozen"] is True
    assert contract["future_outcomes_resolved"] is False
    assert contract["evaluation_metrics_computed"] is False
    assert contract["options_read_for_decision"] is False
    assert contract["futures_read_for_decision"] is False
    assert contract["v3_thresholds_changed"] is False
