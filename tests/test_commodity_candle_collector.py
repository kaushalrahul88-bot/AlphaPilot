import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.commodity_candle_collector import (
    PostgresCandleStore,
    _records,
    backfill_commodity_candles,\n    collect_completed_commodity_candles,
)
from app.main import _collector_store, settings
from fastapi import HTTPException


IST = ZoneInfo("Asia/Kolkata")


def rows(start, count, minutes):
    return [
        [(start + timedelta(minutes=index * minutes)).isoformat(), 100, 102, 99, 101, 10, 20]
        for index in range(count)
    ]


class MemoryStore:
    def __init__(self, latest=None):
        self.initialized = 0
        self.batches = []
        self.latest = latest or {}

    async def initialize(self):
        self.initialized += 1

    async def upsert(self, records):
        self.batches.append(records)
        return len(records)

    async def latest_candle_at(self, trading_symbol, timeframe_minutes):
        return self.latest.get((trading_symbol, timeframe_minutes))

    async def status(self):
        return {"enabled": True, "series": []}


class CommodityCandleCollectorTests(unittest.IsolatedAsyncioTestCase):
    def test_internal_collector_is_fail_closed_without_configuration(self):
        with (
            patch.object(settings, "database_url", ""),
            patch.object(settings, "commodity_collector_token", ""),
            self.assertRaises(HTTPException) as raised,
        ):
            _collector_store(None)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "COLLECTOR_DISABLED")

    def test_internal_collector_rejects_an_invalid_token(self):
        with (
            patch.object(settings, "database_url", "postgresql://configured"),
            patch.object(settings, "commodity_collector_token", "expected"),
            self.assertRaises(HTTPException) as raised,
        ):
            _collector_store("wrong")
        self.assertEqual(raised.exception.status_code, 401)

    def test_postgres_store_requires_database_url(self):
        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            PostgresCandleStore("")

    def test_records_are_canonicalized_and_deduplicated_by_timestamp(self):
        stamp = datetime(2026, 8, 26, 10, 0, tzinfo=IST)
        contract = {
            "exchange": "MCX", "segment": "COMMODITY", "trading_symbol": "CRUDESEP",
            "groww_symbol": "MCX-CRUDE", "expiry_date": date(2026, 9, 21),
        }
        source = [
            [stamp.isoformat(), 100, 102, 99, 101, 10, 20],
            [stamp.isoformat(), 100, 103, 98, 102, 11, 21],
        ]
        records = _records("CRUDEOIL", contract, 5, source, stamp + timedelta(minutes=5))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["close"], 102)
        self.assertEqual(records[0]["open_interest"], 21)
        self.assertEqual(records[0]["trading_symbol"], "CRUDESEP")

    async def test_collection_stores_only_completed_candles_for_all_six_series(self):
        now = datetime(2026, 8, 26, 10, 2, tzinfo=IST)
        five = rows(datetime(2026, 8, 26, 9, 50, tzinfo=IST), 3, 5)
        fifteen = rows(datetime(2026, 8, 26, 9, 30, tzinfo=IST), 3, 15)
        hourly = rows(datetime(2026, 8, 26, 8, 0, tzinfo=IST), 3, 60)
        contracts = [
            {"exchange": "MCX", "segment": "COMMODITY", "trading_symbol": "CRUDESEP", "groww_symbol": "MCX-CRUDE", "expiry_date": "2026-09-21"},
            {"exchange": "MCX", "segment": "COMMODITY", "trading_symbol": "NGSEP", "groww_symbol": "MCX-NG", "expiry_date": "2026-09-24"},
        ]
        store = MemoryStore()
        with (
            patch("app.commodity_candle_collector.resolve_nearest_mcx_future", new=AsyncMock(side_effect=contracts)),
            patch("app.commodity_candle_collector._fetch_chunked", new=AsyncMock(side_effect=[five, fifteen, hourly, five, fifteen, hourly])),
        ):
            result = await collect_completed_commodity_candles(object(), store, now=now)
        self.assertEqual(store.initialized, 1)
        self.assertEqual(len(store.batches), 6)
        self.assertEqual([len(batch) for batch in store.batches], [2, 2, 2, 2, 2, 2])
        self.assertEqual(result["upserted"], 12)
        self.assertEqual(len(result["series"]), 6)
        self.assertEqual(result["idempotency_key"], "provider+trading_symbol+timeframe_minutes+candle_at")
        self.assertNotIn(now.isoformat(), {record["candle_at"].isoformat() for batch in store.batches for record in batch})

    async def test_collection_resumes_with_a_two_candle_overlap(self):
        now = datetime(2026, 8, 26, 10, 2, tzinfo=IST)
        latest = datetime(2026, 8, 26, 9, 55, tzinfo=IST)
        contracts = [
            {"exchange": "MCX", "segment": "COMMODITY", "trading_symbol": "CRUDESEP"},
            {"exchange": "MCX", "segment": "COMMODITY", "trading_symbol": "NGSEP"},
        ]
        store = MemoryStore({
            (contract["trading_symbol"], interval): latest
            for contract in contracts
            for interval in (5, 15, 60)
        })
        fetch = AsyncMock(return_value=[])
        with (
            patch("app.commodity_candle_collector.resolve_nearest_mcx_future", new=AsyncMock(side_effect=contracts)),
            patch("app.commodity_candle_collector._fetch_chunked", new=fetch),
        ):
            result = await collect_completed_commodity_candles(object(), store, now=now)
        self.assertEqual(fetch.await_args_list[0].args[3], latest - timedelta(minutes=10))
        self.assertEqual(fetch.await_args_list[1].args[3], latest - timedelta(minutes=30))
        self.assertEqual(fetch.await_args_list[2].args[3], latest - timedelta(minutes=120))
        self.assertEqual(result["upserted"], 0)


    async def test_copper_backfill_persists_one_bounded_range(self):
        now = datetime(2026, 8, 26, 10, 0, tzinfo=IST)
        source = rows(now - timedelta(hours=1), 10, 5)
        store = MemoryStore()
        contract = {"exchange":"MCX","segment":"COMMODITY","trading_symbol":"COPPERAUG","groww_symbol":"MCX-COPPER","expiry_date":"2026-08-31"}
        with (
            patch("app.commodity_candle_collector.resolve_nearest_mcx_future", new=AsyncMock(return_value=contract)),
            patch("app.commodity_candle_collector._fetch_chunked", new=AsyncMock(return_value=source)),
        ):
            result = await backfill_commodity_candles(
                object(), store, "COPPER", now - timedelta(hours=1), now, 5,
            )
        self.assertEqual(result["status"], "BACKFILLED")
        self.assertEqual(result["symbol"], "COPPER")
        self.assertGreater(result["upserted"], 0)

    async def test_backfill_rejects_more_than_two_days(self):
        store = MemoryStore()
        now = datetime(2026, 8, 26, 10, 0, tzinfo=IST)
        with self.assertRaisesRegex(ValueError, "must not exceed 2 days"):
            await backfill_commodity_candles(object(), store, "COPPER", now - timedelta(days=3), now, 5)
