from datetime import datetime, timezone

import app.crypto_btc_first24h_backtest as replay
from app.crypto_btc_first24h_backtest import CLICK_COUNT, _latest_snapshot, _load_shared_window_start, frozen_clicks


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


def test_lightweight_window_lookup_does_not_load_delta_payloads(monkeypatch):
    start = datetime(2026, 9, 5, 19, 37, 58, tzinfo=timezone.utc)
    executed: list[str] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            del params
            executed.append(str(sql))

        def fetchone(self):
            return (start,)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(replay, "_connect", lambda _database_url: FakeConnection())
    assert _load_shared_window_start("postgresql://unused") == start
    assert len(executed) == 1
    assert "payload" not in executed[0].lower()
    assert "min(first_seen_at)" in executed[0].lower()
