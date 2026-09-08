from datetime import datetime, timezone

from app.crypto_btc_first24h_backtest import CLICK_COUNT, _latest_snapshot, frozen_clicks


def test_frozen_grid_is_exactly_96_end_exclusive_clicks():
    start = datetime(2026, 9, 5, 19, 37, 58, 793829, tzinfo=timezone.utc)
    clicks = frozen_clicks(start)
    assert len(clicks) == CLICK_COUNT == 96
    assert clicks[0] == start
    assert clicks[-1] == start.replace(day=6, hour=19, minute=22)
    assert all((b-a).total_seconds() == 900 for a, b in zip(clicks, clicks[1:]))
    assert all(click < start.replace(day=6, hour=19, minute=37) for click in clicks)


def test_snapshot_selection_never_looks_ahead():
    click = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
    rows = [
        {"at": click.replace(minute=59, hour=19), "payload": {"id": "past"}},
        {"at": click.replace(minute=1), "payload": {"id": "future"}},
    ]
    assert _latest_snapshot(rows, click)["id"] == "past"
