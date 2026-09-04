from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app.copper_pit_candles as pit
from app.copper_candle_observation_store import (
    INSERT_FIRST_SEEN_SQL,
    PROVENANCE_ID,
    CopperCandleObservationStore,
)


IST = ZoneInfo("Asia/Kolkata")


class CopperPITCandleTests(unittest.TestCase):
    def test_direct_store_is_first_seen_only(self):
        sql = " ".join(INSERT_FIRST_SEEN_SQL.split()).upper()
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("DO NOTHING", sql)
        self.assertNotIn("DO UPDATE", sql)

    def test_reader_requires_bar_completion_and_collection_availability(self):
        constants = CopperCandleObservationStore._read_pit_sync.__code__.co_consts
        sql = " ".join(str(value) for value in constants if isinstance(value, str)).upper()
        self.assertIn("COLLECTED_AT <=", sql)
        self.assertIn("INTERVAL '1 MINUTE'", sql)
        self.assertIn("TIMEFRAME_MINUTES = %S", sql)

    def test_store_rejects_non_copper_before_database_access(self):
        with self.assertRaisesRegex(ValueError, "COPPER"):
            CopperCandleObservationStore._validate_record(
                {
                    "symbol": "CRUDEOIL",
                    "timeframe_minutes": 5,
                    "trading_symbol": "CRUDEOIL21SEP26FUT",
                }
            )

    def test_first_collection_uses_only_six_hour_warmup_and_true_collection_time(self):
        now = datetime(2026, 9, 4, 14, 30, tzinfo=IST)
        calls = {}

        class FakeStore:
            async def initialize(self):
                calls["initialized"] = True

            async def latest_candle_at(self, trading_symbol):
                calls["latest_symbol"] = trading_symbol
                return None

            async def insert_first_seen(self, records):
                calls["records"] = records
                return len(records)

            async def status(self):
                return {"rows": len(calls.get("records", []))}

        async def fake_resolve(symbol):
            self.assertEqual(symbol, "COPPER")
            return {
                "exchange": "MCX",
                "segment": "COMMODITY",
                "trading_symbol": "COPPER30SEP26FUT",
                "groww_symbol": "MCX-COPPER30SEP26FUT",
                "expiry_date": "2026-09-30",
            }

        async def fake_fetch(provider, contract, interval, start, end):
            calls["fetch"] = (provider, contract, interval, start, end)
            return [
                ["2026-09-04T14:20:00+05:30", 900, 902, 899, 901, 100, 5000],
            ]

        with patch.object(pit, "resolve_nearest_mcx_future", fake_resolve), patch.object(
            pit, "_fetch_chunked", fake_fetch
        ):
            result = asyncio.run(pit.collect_copper_pit_candles("provider", FakeStore(), now))

        _, contract, interval, fetch_start, fetch_end = calls["fetch"]
        self.assertEqual(contract["trading_symbol"], "COPPER30SEP26FUT")
        self.assertEqual(interval, 5)
        self.assertEqual(fetch_start, now - timedelta(hours=6))
        self.assertEqual(fetch_end, now)
        self.assertTrue(result["first_run_bounded_warmup"])
        self.assertEqual(result["warmup_hours"], 6)
        self.assertFalse(result["warmup_market_timestamps_retroactively_visible"])
        self.assertFalse(result["historical_backfill_used"])
        self.assertFalse(result["generic_all_commodity_collector_used"])
        self.assertEqual(result["provenance_id"], PROVENANCE_ID)
        self.assertEqual(calls["records"][0]["collected_at"], now)

    def test_subsequent_collection_is_bounded_to_ten_minute_overlap(self):
        now = datetime(2026, 9, 4, 15, 0, tzinfo=IST)
        latest = datetime(2026, 9, 4, 14, 50, tzinfo=IST)
        calls = {}

        class FakeStore:
            async def initialize(self):
                pass

            async def latest_candle_at(self, trading_symbol):
                return latest

            async def insert_first_seen(self, records):
                return len(records)

            async def status(self):
                return {"rows": 10}

        async def fake_resolve(symbol):
            return {
                "exchange": "MCX",
                "segment": "COMMODITY",
                "trading_symbol": "COPPER30SEP26FUT",
                "groww_symbol": "MCX-COPPER30SEP26FUT",
                "expiry_date": "2026-09-30",
            }

        async def fake_fetch(provider, contract, interval, start, end):
            calls["start"] = start
            calls["end"] = end
            return []

        with patch.object(pit, "resolve_nearest_mcx_future", fake_resolve), patch.object(
            pit, "_fetch_chunked", fake_fetch
        ):
            result = asyncio.run(pit.collect_copper_pit_candles("provider", FakeStore(), now))

        self.assertEqual(calls["start"], latest - timedelta(minutes=10))
        self.assertEqual(calls["end"], now)
        self.assertFalse(result["first_run_bounded_warmup"])
        self.assertEqual(result["warmup_hours"], 0)
        self.assertEqual(result["overlap_minutes"], 10)

    def test_closed_market_makes_no_provider_or_store_call(self):
        saturday = datetime(2026, 9, 5, 12, 0, tzinfo=IST)

        class ExplodingStore:
            async def initialize(self):
                raise AssertionError("closed market must not initialize store")

        result = asyncio.run(
            pit.collect_copper_pit_candles("provider", ExplodingStore(), saturday)
        )
        self.assertEqual(result["status"], "MARKET_CLOSED")
        self.assertFalse(result["live_execution_enabled"])
        self.assertFalse(result["broker_order_placement_enabled"])
        self.assertFalse(result["historical_backfill_used"])


if __name__ == "__main__":
    unittest.main()
