from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import app.crude_oil_mini_live_inputs as live_inputs
from app.crude_oil_mini_option_observation_store import PROVENANCE_ID, TABLE_NAME


IST = ZoneInfo("Asia/Kolkata")


class _Cursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return []


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def _row(bucket: datetime, *, symbol: str, option_type: str, strike: float, collected_at: datetime):
    return {
        "trading_symbol": symbol,
        "expiry_date": "2026-09-17",
        "strike": strike,
        "option_type": option_type,
        "lot_size": 10,
        "sample_bucket_at": bucket.isoformat(),
        "observed_at": (bucket + timedelta(seconds=5)).isoformat(),
        "collected_at": collected_at.isoformat(),
        "underlying_price": 8700.0,
        "last_price": 180.0 if option_type == "CE" else 120.0,
        "volume": 100.0,
        "open_interest": 1000.0,
        "bid_price": None,
        "ask_price": None,
    }


def test_option_reader_uses_only_immutable_mini_table(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(live_inputs, "_connect", lambda _database_url: _Connection(cursor))
    click = datetime(2026, 9, 4, 16, 25, tzinfo=IST)

    rows = live_inputs._read_option_rows_as_of_sync("postgresql://test", click)

    assert rows == []
    assert cursor.sql.count(TABLE_NAME) == 2
    assert "FROM commodity_option_snapshots" not in cursor.sql
    assert "underlying_symbol = 'CRUDEOILM'" in cursor.sql
    assert len(cursor.params) == 8
    assert all(value == click for value in (cursor.params[1], cursor.params[2], cursor.params[3], cursor.params[5], cursor.params[6], cursor.params[7]))


def test_verified_empty_state_never_falls_back_to_mutable_generic_snapshot():
    result = live_inputs.summarize_option_positioning(
        [],
        "2026-09-04T16:10:00+05:30",
        immutable_provenance_verified=True,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "NO_IMMUTABLE_PIT_OPTION_SNAPSHOT"
    assert result["source_table"] == TABLE_NAME
    assert result["first_seen_immutable"] is True
    assert result["provenance_id"] == PROVENANCE_ID
    assert result["historical_backfill_used"] is False
    assert result["mutable_generic_fallback_used"] is False


def test_available_at_is_first_seen_collection_time_not_bucket_time():
    bucket = datetime(2026, 9, 4, 16, 20, tzinfo=IST)
    collected_ce = bucket + timedelta(minutes=1, seconds=10)
    collected_pe = bucket + timedelta(minutes=1, seconds=16)
    rows = [
        _row(
            bucket,
            symbol="CRUDEOILM17SEP268700CE",
            option_type="CE",
            strike=8700,
            collected_at=collected_ce,
        ),
        _row(
            bucket,
            symbol="CRUDEOILM17SEP268700PE",
            option_type="PE",
            strike=8700,
            collected_at=collected_pe,
        ),
    ]

    result = live_inputs.summarize_option_positioning(
        rows,
        bucket + timedelta(minutes=3),
        immutable_provenance_verified=True,
    )
    context = live_inputs.option_context_record(result)

    assert result["status"] == "AVAILABLE"
    assert result["available_at"] == collected_pe.isoformat()
    assert result["first_seen_immutable"] is True
    assert context["available_at"] == collected_pe.isoformat()
    assert context["quality"] == "PIT_FIRST_SEEN_IMMUTABLE"
    assert context["value"]["provenance_id"] == PROVENANCE_ID


def test_synthetic_rows_are_not_falsely_certified_as_db_immutable():
    bucket = datetime(2026, 9, 4, 16, 20, tzinfo=IST)
    result = live_inputs.summarize_option_positioning(
        [
            _row(
                bucket,
                symbol="CRUDEOILM17SEP268700CE",
                option_type="CE",
                strike=8700,
                collected_at=bucket + timedelta(seconds=30),
            )
        ],
        bucket + timedelta(minutes=1),
    )

    assert result["first_seen_immutable"] is False
    assert result["source_table"] is None
    assert result["provenance_id"] is None
    assert result["mutable_generic_fallback_used"] is False
