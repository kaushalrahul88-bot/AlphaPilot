from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app.crude_oil_mini_pit_candles as pit

IST = ZoneInfo("Asia/Kolkata")


class CrudeOilMiniPITCandleTests(unittest.TestCase):
    def test_crude_mini_pit_sql_preserves_first_seen_state_and_time(self):
        sql = " ".join(pit.CRUDE_MINI_PIT_UPSERT_SQL.split()).upper()
        self.assertIn(
            "LEAST(COMMODITY_CANDLES.COLLECTED_AT, EXCLUDED.COLLECTED_AT)",
            sql,
        )
        self.assertNotIn("COLLECTED_AT = EXCLUDED.COLLECTED_AT", sql)
        for column in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "OPEN_INTEREST"):
            self.assertNotIn(f"{column} = EXCLUDED.{column}", sql)

    def test_crude_mini_pit_reader_gates_availability_and_bar_close(self):
        source = pit.CrudeOilMiniPITStore._read_symbol_pit_sync.__code__.co_consts
        sql = " ".join(
            str(value) for value in source if isinstance(value, str)
        ).upper()
        self.assertIn("COLLECTED_AT <=", sql)
        self.assertIn("INTERVAL '1 MINUTE'", sql)
        self.assertIn("SYMBOL = %S", sql)

    def test_crude_mini_store_rejects_regular_crude_before_database_access(self):
        store = object.__new__(pit.CrudeOilMiniPITStore)
        with self.assertRaisesRegex(ValueError, "CRUDEOILM"):
            store._upsert_sync([{"symbol": "CRUDEOIL"}])

    def test_live_collection_is_bounded_and_never_uses_regular_crude(self):
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
            self.assertEqual(observed, now)
            return {
                "exchange": "MCX",
                "segment": "COMMODITY",
                "trading_symbol": "CRUDEOILM21SEP26FUT",
                "groww_symbol": "MCX-CRUDEOILM21SEP26FUT",
                "expiry_date": "2026-09-21",
            }

        async def fake_fetch(provider, contract, interval, start, end):
            calls["fetch"] = (provider, contract, interval, start, end)
            return [
                ["2026-09-04T14:20:00+05:30", 8600, 8610, 8590, 8605, 10]
            ]

        with patch.object(
            pit,
            "resolve_current_crude_oil_mini_future",
            fake_resolve,
        ), patch.object(pit, "_fetch_chunked", fake_fetch):
            result = asyncio.run(
                pit.collect_crude_oil_mini_pit_candles(
                    "provider",
                    FakeStore(),
                    now,
                )
            )

        _, contract, interval, fetch_start, fetch_end = calls["fetch"]
        self.assertTrue(contract["trading_symbol"].startswith("CRUDEOILM"))
        self.assertNotIn("CRUDEOIL21", contract["trading_symbol"])
        self.assertEqual(interval, 5)
        self.assertEqual(
            fetch_start,
            datetime(2026, 9, 4, 14, 10, tzinfo=IST),
        )
        self.assertEqual(fetch_end, now)
        self.assertFalse(result["regular_crude_alias_allowed"])
        self.assertEqual(
            result["pit_provenance"],
            "FIRST_SEEN_CANDLE_STATE_IMMUTABLE",
        )

    def test_pit_read_passes_exact_as_of_to_store(self):
        as_of = datetime(2026, 9, 4, 15, 17, tzinfo=IST)
        calls = {}

        class FakeStore:
            async def read_symbol_pit(
                self,
                symbol,
                timeframe,
                start,
                end,
                observed,
            ):
                calls["args"] = (symbol, timeframe, start, end, observed)
                return [
                    ["2026-09-04T15:10:00+05:30", 1, 2, 0.5, 1.5, 3, None]
                ]

        rows = asyncio.run(
            pit.read_crude_oil_mini_pit_candles(FakeStore(), as_of)
        )
        self.assertTrue(rows)
        symbol, timeframe, _, end, observed = calls["args"]
        self.assertEqual(symbol, "CRUDEOILM")
        self.assertEqual(timeframe, 5)
        self.assertEqual(end, as_of)
        self.assertEqual(observed, as_of)


if __name__ == "__main__":
    unittest.main()
