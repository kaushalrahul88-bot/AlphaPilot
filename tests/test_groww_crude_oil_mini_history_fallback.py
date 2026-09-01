from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.providers.groww_rate_limited import RateLimitedGrowwProvider

IST = ZoneInfo("Asia/Kolkata")


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    responses = []
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None, params=None):
        self.__class__.calls.append((url, dict(params or {})))
        return self.__class__.responses.pop(0)


class MiniHistoryFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeClient.calls = []
        FakeClient.responses = []
        self.provider = object.__new__(RateLimitedGrowwProvider)
        self.provider._headers = AsyncMock(return_value={"Authorization": "Bearer test"})

    def test_history_bounds_use_calendar_anchor_and_latest_completed_bar(self):
        start_a, end_a = RateLimitedGrowwProvider._mini_history_bounds(
            observed_at=datetime(2026, 9, 1, 16, 29, 47, 123456, tzinfo=IST),
            lookback_days=180,
            interval_minutes=5,
        )
        start_b, end_b = RateLimitedGrowwProvider._mini_history_bounds(
            observed_at=datetime(2026, 9, 1, 16, 31, 2, tzinfo=IST),
            lookback_days=180,
            interval_minutes=5,
        )
        self.assertEqual(start_a, datetime(2026, 3, 5, 0, 0, tzinfo=IST))
        self.assertEqual(start_b, start_a)
        self.assertEqual(end_a, datetime(2026, 9, 1, 16, 20, tzinfo=IST))
        self.assertEqual(end_b, datetime(2026, 9, 1, 16, 25, tzinfo=IST))
        self.assertEqual(start_a.second, 0)
        self.assertEqual(start_a.microsecond, 0)

    async def test_legacy_range_is_used_when_modern_route_is_empty(self):
        FakeClient.responses = [
            FakeResponse(200, {"status": "SUCCESS", "payload": {"candles": []}}),
            FakeResponse(200, {"status": "SUCCESS", "payload": {"candles": [["2026-08-31T09:00:00", 8200, 8210, 8190, 8205, 100]]}}),
        ]
        contract = {
            "instrument_type": "FUT",
            "trading_symbol": "CRUDEOILM21SEP26FUT",
            "groww_symbol": "MCX-CRUDEOILM-21Sep26-FUT",
        }
        with patch.object(RateLimitedGrowwProvider, "_throttle", new=AsyncMock()), \
             patch("app.providers.groww_rate_limited.httpx.AsyncClient", FakeClient):
            rows = await self.provider._mini_fetch_chunk(
                contract,
                candle_interval="5minute",
                legacy_minutes=5,
                start=datetime(2026, 8, 31, 9, 0, tzinfo=IST),
                end=datetime(2026, 8, 31, 23, 30, tzinfo=IST),
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(FakeClient.calls), 2)
        self.assertTrue(FakeClient.calls[0][0].endswith("/v1/historical/candles"))
        self.assertTrue(FakeClient.calls[1][0].endswith("/v1/historical/candle/range"))
        self.assertEqual(FakeClient.calls[1][1]["trading_symbol"], "CRUDEOILM21SEP26FUT")
        self.assertEqual(FakeClient.calls[1][1]["interval_in_minutes"], "5")

    async def test_future_merges_partial_modern_with_legacy_and_modern_wins_overlap(self):
        FakeClient.responses = [
            FakeResponse(200, {"status": "SUCCESS", "payload": {"candles": [
                ["2026-08-31T09:05:00", 8205, 8215, 8195, 8210, 200],
                ["2026-09-01T09:00:00", 8300, 8310, 8290, 8305, 300],
            ]}}),
            FakeResponse(200, {"status": "SUCCESS", "payload": {"candles": [
                ["2026-08-31T09:00:00", 8200, 8210, 8190, 8205, 100],
                ["2026-08-31T09:05:00", 1, 2, 0.5, 1.5, 999],
            ]}}),
        ]
        contract = {
            "instrument_type": "FUT",
            "trading_symbol": "CRUDEOILM21SEP26FUT",
            "groww_symbol": "MCX-CRUDEOILM-21Sep26-FUT",
        }
        with patch.object(RateLimitedGrowwProvider, "_throttle", new=AsyncMock()), \
             patch("app.providers.groww_rate_limited.httpx.AsyncClient", FakeClient):
            rows = await self.provider._mini_fetch_chunk(
                contract,
                candle_interval="5minute",
                legacy_minutes=5,
                start=datetime(2026, 8, 31, 9, 0, tzinfo=IST),
                end=datetime(2026, 9, 1, 12, 0, tzinfo=IST),
            )
        self.assertEqual(len(FakeClient.calls), 2)
        self.assertEqual(len(rows), 3)
        by_stamp = {row[0]: row for row in rows}
        overlap = "2026-08-31T09:05:00+05:30"
        self.assertIn(overlap, by_stamp)
        self.assertEqual(by_stamp[overlap][4], 8210)
        self.assertEqual(by_stamp[overlap][5], 200)
        self.assertIn("2026-08-31T09:00:00+05:30", by_stamp)
        self.assertIn("2026-09-01T09:00:00+05:30", by_stamp)

    async def test_option_modern_history_still_short_circuits_legacy_route(self):
        FakeClient.responses = [
            FakeResponse(200, {"status": "SUCCESS", "payload": {"candles": [["2026-09-01T09:00:00", 100, 110, 90, 105, 10]]}}),
        ]
        contract = {
            "instrument_type": "CE",
            "trading_symbol": "CRUDEOILM17SEP268250CE",
            "groww_symbol": "MCX-CRUDEOILM-17Sep26-8250-CE",
        }
        with patch.object(RateLimitedGrowwProvider, "_throttle", new=AsyncMock()), \
             patch("app.providers.groww_rate_limited.httpx.AsyncClient", FakeClient):
            rows = await self.provider._mini_fetch_chunk(
                contract,
                candle_interval="5minute",
                legacy_minutes=5,
                start=datetime(2026, 9, 1, 9, 0, tzinfo=IST),
                end=datetime(2026, 9, 1, 12, 0, tzinfo=IST),
                tolerate_legacy_miss=True,
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(FakeClient.calls), 1)

    async def test_option_prelisting_legacy_miss_is_tolerated(self):
        FakeClient.responses = [
            FakeResponse(200, {"status": "SUCCESS", "payload": {"candles": []}}),
            FakeResponse(404, {"status": "FAILURE", "message": "contract unavailable for interval"}),
        ]
        contract = {
            "instrument_type": "CE",
            "trading_symbol": "CRUDEOILM17SEP268250CE",
            "groww_symbol": "MCX-CRUDEOILM-17Sep26-8250-CE",
        }
        with patch.object(RateLimitedGrowwProvider, "_throttle", new=AsyncMock()), \
             patch("app.providers.groww_rate_limited.httpx.AsyncClient", FakeClient):
            rows = await self.provider._mini_fetch_chunk(
                contract,
                candle_interval="5minute",
                legacy_minutes=5,
                start=datetime(2026, 7, 1, 9, 0, tzinfo=IST),
                end=datetime(2026, 7, 7, 23, 30, tzinfo=IST),
                tolerate_legacy_miss=True,
            )
        self.assertEqual(rows, [])
        self.assertEqual(len(FakeClient.calls), 2)

    async def test_future_legacy_error_fails_closed_even_when_modern_has_rows(self):
        FakeClient.responses = [
            FakeResponse(200, {"status": "SUCCESS", "payload": {"candles": [["2026-09-01T09:00:00", 8300, 8310, 8290, 8305, 300]]}}),
            FakeResponse(429, {"status": "FAILURE", "message": "rate limited"}),
        ]
        contract = {
            "instrument_type": "FUT",
            "trading_symbol": "CRUDEOILM21SEP26FUT",
            "groww_symbol": "MCX-CRUDEOILM-21Sep26-FUT",
        }
        with patch.object(RateLimitedGrowwProvider, "_throttle", new=AsyncMock()), \
             patch("app.providers.groww_rate_limited.httpx.AsyncClient", FakeClient):
            with self.assertRaises(RuntimeError):
                await self.provider._mini_fetch_chunk(
                    contract,
                    candle_interval="5minute",
                    legacy_minutes=5,
                    start=datetime(2026, 9, 1, 9, 0, tzinfo=IST),
                    end=datetime(2026, 9, 1, 12, 0, tzinfo=IST),
                )

    async def test_future_modern_error_fails_closed_even_when_legacy_has_rows(self):
        FakeClient.responses = [
            FakeResponse(429, {"status": "FAILURE", "message": "rate limited"}),
            FakeResponse(200, {"status": "SUCCESS", "payload": {"candles": [["2026-09-01T09:00:00", 8300, 8310, 8290, 8305, 300]]}}),
        ]
        contract = {
            "instrument_type": "FUT",
            "trading_symbol": "CRUDEOILM21SEP26FUT",
            "groww_symbol": "MCX-CRUDEOILM-21Sep26-FUT",
        }
        with patch.object(RateLimitedGrowwProvider, "_throttle", new=AsyncMock()), \
             patch("app.providers.groww_rate_limited.httpx.AsyncClient", FakeClient):
            with self.assertRaises(RuntimeError):
                await self.provider._mini_fetch_chunk(
                    contract,
                    candle_interval="5minute",
                    legacy_minutes=5,
                    start=datetime(2026, 9, 1, 9, 0, tzinfo=IST),
                    end=datetime(2026, 9, 1, 12, 0, tzinfo=IST),
                )

    async def test_future_legacy_error_still_fails_closed_when_modern_is_empty(self):
        FakeClient.responses = [
            FakeResponse(200, {"status": "SUCCESS", "payload": {"candles": []}}),
            FakeResponse(404, {"status": "FAILURE", "message": "unexpected future history failure"}),
        ]
        contract = {
            "instrument_type": "FUT",
            "trading_symbol": "CRUDEOILM21SEP26FUT",
            "groww_symbol": "MCX-CRUDEOILM-21Sep26-FUT",
        }
        with patch.object(RateLimitedGrowwProvider, "_throttle", new=AsyncMock()), \
             patch("app.providers.groww_rate_limited.httpx.AsyncClient", FakeClient):
            with self.assertRaises(RuntimeError):
                await self.provider._mini_fetch_chunk(
                    contract,
                    candle_interval="5minute",
                    legacy_minutes=5,
                    start=datetime(2026, 8, 1, 9, 0, tzinfo=IST),
                    end=datetime(2026, 8, 7, 23, 30, tzinfo=IST),
                )


if __name__ == "__main__":
    unittest.main()
