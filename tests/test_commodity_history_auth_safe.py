import unittest
from unittest.mock import patch

import httpx

import app.commodity_history_auth_safe as safe


class DummyProvider:
    def __init__(self):
        self.throttle_calls = 0
        self.rate_limit_calls = 0

    async def _throttle(self):
        self.throttle_calls += 1

    async def _register_rate_limit(self):
        self.rate_limit_calls += 1


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.groww.in/test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


class CommodityHistoryAuthSafeTests(unittest.IsolatedAsyncioTestCase):
    async def test_401_refreshes_once_and_retries_same_fetch(self):
        provider = DummyProvider()
        attempts = 0
        refreshes = 0
        expected = [["2026-09-07T11:00:00+05:30", 1, 2, 0.5, 1.5, 10]]

        async def fake_fetch(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise _http_error(401)
            return expected

        async def fake_refresh(value):
            nonlocal refreshes
            self.assertIs(value, provider)
            refreshes += 1
            return "TOTP"

        with patch.object(safe, "_refresh_after_401", fake_refresh):
            result = await safe.fetch_chunked_auth_safe(
                provider,
                {"trading_symbol": "COPPER30SEP26FUT"},
                5,
                object(),
                object(),
                fetcher=fake_fetch,
            )

        self.assertEqual(result, expected)
        self.assertEqual(attempts, 2)
        self.assertEqual(refreshes, 1)
        self.assertEqual(provider.throttle_calls, 2)
        self.assertEqual(provider.rate_limit_calls, 0)

    async def test_persistent_401_fails_closed_after_one_refresh(self):
        provider = DummyProvider()
        attempts = 0
        refreshes = 0

        async def fake_fetch(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            raise _http_error(401)

        async def fake_refresh(value):
            nonlocal refreshes
            self.assertIs(value, provider)
            refreshes += 1
            return "TOTP"

        with patch.object(safe, "_refresh_after_401", fake_refresh):
            with self.assertRaises(httpx.HTTPStatusError) as caught:
                await safe.fetch_chunked_auth_safe(
                    provider,
                    {"trading_symbol": "COPPER30SEP26FUT"},
                    5,
                    object(),
                    object(),
                    fetcher=fake_fetch,
                )

        self.assertEqual(caught.exception.response.status_code, 401)
        self.assertEqual(attempts, 2)
        self.assertEqual(refreshes, 1)
        self.assertEqual(provider.throttle_calls, 2)
        self.assertEqual(provider.rate_limit_calls, 0)

    async def test_429_registers_shared_cooldown_without_auth_refresh(self):
        provider = DummyProvider()
        refreshes = 0

        async def fake_fetch(*args, **kwargs):
            raise _http_error(429)

        async def fake_refresh(value):
            nonlocal refreshes
            refreshes += 1

        with patch.object(safe, "_refresh_after_401", fake_refresh):
            with self.assertRaises(httpx.HTTPStatusError) as caught:
                await safe.fetch_chunked_auth_safe(
                    provider,
                    {"trading_symbol": "COPPER30SEP26FUT"},
                    5,
                    object(),
                    object(),
                    fetcher=fake_fetch,
                )

        self.assertEqual(caught.exception.response.status_code, 429)
        self.assertEqual(refreshes, 0)
        self.assertEqual(provider.throttle_calls, 1)
        self.assertEqual(provider.rate_limit_calls, 1)

    def test_architecture_contract_is_operational_only(self):
        contract = safe.architecture_contract()
        self.assertFalse(contract["historical_fetch_semantics_changed"])
        self.assertFalse(contract["strategy_rules_changed"])
        self.assertFalse(contract["contract_selection_changed"])
        self.assertFalse(contract["pit_visibility_changed"])
        self.assertEqual(contract["retry_on_401"], 1)
        self.assertTrue(contract["shared_throttle_used"])
        self.assertTrue(contract["shared_429_cooldown_registered"])
        self.assertFalse(contract["live_execution_enabled"])
        self.assertFalse(contract["broker_order_placement_enabled"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
