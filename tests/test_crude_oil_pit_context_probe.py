from datetime import datetime, timezone

from app.crude_oil_pit_context_probe import _normalize_chart


def test_hourly_context_bar_is_visible_only_after_completion():
    epoch = int(datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc).timestamp())
    result = {
        "timestamp": [epoch],
        "indicators": {
            "quote": [{
                "open": [80.0],
                "high": [81.0],
                "low": [79.0],
                "close": [80.5],
                "volume": [100.0],
            }]
        },
    }
    rows = _normalize_chart(result, bar_minutes=60)
    assert len(rows) == 1
    assert rows[0]["bar_start"] == "2026-08-31T17:30:00+05:30"
    assert rows[0]["available_at"] == "2026-08-31T18:30:00+05:30"


def test_probe_normalization_drops_incomplete_ohlc_and_dedupes_timestamp():
    start = int(datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc).timestamp())
    result = {
        "timestamp": [start, start, start + 3600],
        "indicators": {
            "quote": [{
                "open": [80.0, 80.1, 81.0],
                "high": [81.0, 81.1, None],
                "low": [79.0, 79.1, 80.0],
                "close": [80.5, 80.6, 80.8],
                "volume": [100.0, 101.0, 102.0],
            }]
        },
    }
    rows = _normalize_chart(result, bar_minutes=60)
    assert len(rows) == 1
    assert rows[0]["open"] == 80.0
