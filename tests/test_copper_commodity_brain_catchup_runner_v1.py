from datetime import date

from app.copper_commodity_brain_catchup_runner_v1 import REPLAY_DATES, scheduled_clicks


def test_replay_dates_exclude_incomplete_august_contract_day():
    assert date(2026, 8, 31) not in REPLAY_DATES
    assert REPLAY_DATES == (
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
        date(2026, 9, 4),
    )


def test_click_schedule_matches_crude_research_cadence():
    clicks = scheduled_clicks(date(2026, 9, 1))
    assert len(clicks) == 29
    assert clicks[0].hour == 9 and clicks[0].minute == 0
    assert clicks[-1].hour == 23 and clicks[-1].minute == 0
    assert all((b - a).total_seconds() == 1800 for a, b in zip(clicks, clicks[1:]))
