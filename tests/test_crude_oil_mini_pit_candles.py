from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import app.crude_oil_mini_pit_candles as pit

IST = ZoneInfo("Asia/Kolkata")


def test_crude_mini_pit_sql_preserves_earliest_observation():
    sql = " ".join(pit.CRUDE_MINI_PIT_UPSERT_SQL.split()).upper()
    assert "LEAST(COMMODITY_CANDLES.COLLECTED_AT, EXCLUDED.COLLECTED_AT)" in sql
    assert "COLLECTED_AT = EXCLUDED.COLLECTED_AT" not in sql


def test_crude_mini_pit_reader_gates_availability_and_bar_close():
    source = pit.CrudeOilMiniPITStore._read_symbol_pit_sync.__code__.co_consts
    sql = " ".join(str(value) for value in source if isinstance(value, str)).upper()
    assert "COLLECTED_AT <=" in sql
    assert "INTERVAL '1 MINUTE'" in sql
    assert "SYMBOL = %S" in sql


def test_crude_mini_store_rejects_regular_crude_before_database_access():
    store = object.__new__(pit.CrudeOilMiniPITStore)
    with pytest.raises(ValueError, match="CRUDEOILM"):
        store._upsert_sync([{"symbol": "CRUDEOIL"}])


def test_live_collection_is_bounded_and_never_uses_regular_crude(monkeypatch):
    now = datetime(2026, 9, 4, 14, 30, tzinfo=IST)
    latest = datetime(2026, 9, 4, 14, 20, tzinfo=IST)
    calls = {}

    class FakeStore:
        async def initialize(self):
            calls["initialized"] = True

        async def latest_candle_at(self, trading_symbol, timeframe_minutes):
            calls["latest_args"] = (trading_symbol, timeframe_minutes)
            return latest

        async def upsert(self, records):
            calls["records"] = records
            return len(records)

    async def fake_resolve(observed):
        assert observed == now
        return {
            "exchange": "MCX",
            "segment": "COMMODITY",
            "trading_symbol": "CRUDEOILM21SEP26FUT",
            "groww_symbol": "MCX-CRUDEOILM21SEP26FUT",
            "expiry_date": "2026-09-21",
        }

    async def fake_fetch(provider, contract, interval, start, end):
        calls["fetch"] = (provider, contract, interval, start, end)
        return [["2026-09-04T14:20:00+05:30", 8600, 8610, 8590, 8605, 10]]

    monkeypatch.setattr(pit, "resolve_current_crude_oil_mini_future", fake_resolve)
    monkeypatch.setattr(pit, "_fetch_chunked", fake_fetch)

    result = asyncio.run(
        pit.collect_crude_oil_mini_pit_candles("provider", FakeStore(), now)
    )

    _, contract, interval, fetch_start, fetch_end = calls["fetch"]
    assert contract["trading_symbol"].startswith("CRUDEOILM")
    assert "CRUDEOIL21" not in contract["trading_symbol"]
    assert interval == 5
    assert fetch_start == datetime(2026, 9, 4, 14, 10, tzinfo=IST)
    assert fetch_end == now
    assert result["regular_crude_alias_allowed"] is False
    assert result["pit_provenance"] == "EARLIEST_COLLECTED_AT_PRESERVED"


def test_pit_read_passes_exact_as_of_to_store():
    as_of = datetime(2026, 9, 4, 15, 17, tzinfo=IST)
    calls = {}

    class FakeStore:
        async def read_symbol_pit(self, symbol, timeframe, start, end, observed):
            calls["args"] = (symbol, timeframe, start, end, observed)
            return [["2026-09-04T15:10:00+05:30", 1, 2, 0.5, 1.5, 3, None]]

    rows = asyncio.run(pit.read_crude_oil_mini_pit_candles(FakeStore(), as_of))
    assert rows
    symbol, timeframe, _, end, observed = calls["args"]
    assert symbol == "CRUDEOILM"
    assert timeframe == 5
    assert end == as_of
    assert observed == as_of
