from __future__ import annotations

import asyncio
import time
import unittest

import httpx

from app.providers.groww_account_safe import AccountSafeGrowwProvider


class AccountSafeGrowwProviderTests(unittest.TestCase):
    def tearDown(self):
        AccountSafeGrowwProvider._blocked_until = 0.0

    def test_account_safe_limits_are_below_previous_burst_ceiling(self):
        self.assertLessEqual(AccountSafeGrowwProvider.MAX_PER_SECOND, 2)
        self.assertLessEqual(AccountSafeGrowwProvider.MAX_PER_MINUTE, 45)
        self.assertGreaterEqual(AccountSafeGrowwProvider.RATE_LIMIT_COOLDOWN_SECONDS, 60)

    def test_429_registers_shared_cooldown(self):
        async def scenario():
            AccountSafeGrowwProvider._blocked_until = 0.0
            request = httpx.Request("GET", "https://api.groww.in/v1/live-data/quote")
            response = httpx.Response(429, request=request)

            async def fail():
                raise httpx.HTTPStatusError("rate limited", request=request, response=response)

            before = time.monotonic()
            with self.assertRaises(httpx.HTTPStatusError):
                await AccountSafeGrowwProvider._guarded(fail)

            self.assertGreaterEqual(
                AccountSafeGrowwProvider._blocked_until,
                before + AccountSafeGrowwProvider.RATE_LIMIT_COOLDOWN_SECONDS - 1.0,
            )

        asyncio.run(scenario())

    def test_non_429_does_not_register_cooldown(self):
        async def scenario():
            AccountSafeGrowwProvider._blocked_until = 0.0
            request = httpx.Request("GET", "https://api.groww.in/v1/live-data/quote")
            response = httpx.Response(500, request=request)

            async def fail():
                raise httpx.HTTPStatusError("server error", request=request, response=response)

            with self.assertRaises(httpx.HTTPStatusError):
                await AccountSafeGrowwProvider._guarded(fail)

            self.assertEqual(AccountSafeGrowwProvider._blocked_until, 0.0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
