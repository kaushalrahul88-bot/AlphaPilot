from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_oil_mini_option_observation_store import (
    MINI_SCHEMA_SQL,
    PROVENANCE_ID,
    TABLE_NAME,
    TRIGGER_SQL,
)
from app.crude_oil_mini_option_premium_memory_v1 import READ_SQL, analyze_premium_memory_rows


IST = ZoneInfo("Asia/Kolkata")


def _row(stamp):
    return {
        "underlying_symbol": "CRUDEOILM",
        "trading_symbol": "CRUDEOILM17SEP268600CE",
        "expiry_date": "2026-09-17",
        "strike": 8600,
        "option_type": "CE",
        "lot_size": 10,
        "sample_bucket_at": stamp.isoformat(),
        "observed_at": (stamp + timedelta(seconds=5)).isoformat(),
        "collected_at": (stamp + timedelta(seconds=10)).isoformat(),
        "underlying_price": 8600,
        "last_price": 300,
        "volume": 1000,
        "open_interest": 500,
        "bid_price": None,
        "ask_price": None,
    }


def test_mini_store_is_separate_and_first_seen_only():
    assert TABLE_NAME == "crude_oil_mini_option_observations"
    assert "CHECK (underlying_symbol = 'CRUDEOILM')" in MINI_SCHEMA_SQL
    assert "PRIMARY KEY (provider, trading_symbol, sample_bucket_at)" in MINI_SCHEMA_SQL
    assert f"INSERT INTO {TABLE_NAME}" in TRIGGER_SQL
    assert "ON CONFLICT (provider, trading_symbol, sample_bucket_at) DO NOTHING" in TRIGGER_SQL
    assert "AFTER INSERT ON commodity_option_snapshots" in TRIGGER_SQL
    assert "AFTER UPDATE" not in TRIGGER_SQL
    assert "INSERT INTO commodity_option_snapshots" not in TRIGGER_SQL
    assert "SELECT" not in TRIGGER_SQL.upper()


def test_premium_memory_reads_only_immutable_mini_store():
    assert f"FROM {TABLE_NAME}" in READ_SQL
    assert "FROM commodity_option_snapshots" not in READ_SQL


def test_provenance_is_asserted_only_for_verified_store_rows():
    stamp = datetime(2026, 9, 4, 14, 0, tzinfo=IST)
    unverified = analyze_premium_memory_rows(
        [_row(stamp)],
        as_of=stamp + timedelta(minutes=1),
    )
    verified = analyze_premium_memory_rows(
        [_row(stamp)],
        as_of=stamp + timedelta(minutes=1),
        provenance_verified=True,
    )

    assert unverified["first_seen_immutable"] is False
    assert unverified["provenance_id"] == "UNVERIFIED_CALLER_ROWS"
    assert verified["first_seen_immutable"] is True
    assert verified["provenance_id"] == PROVENANCE_ID
    assert verified["historical_backfill_used"] is False
    assert verified["promotion_eligible"] is False
    assert verified["risk_translation_effect"] == "NONE"
    assert verified["live_execution_enabled"] is False
    assert verified["broker_order_placement_enabled"] is False
