import asyncio
import os
import unittest
from unittest.mock import patch

import httpx

from app.providers.groww_autoauth import (
    AutoAuthAmountAwareGrowwProvider,
    GrowwAuthRateLimitedError,
)


class GrowwAutoAuthTests(unittest.TestCase):
    def setUp(self):
        AutoAuthAmountAwareGrowwProvider._shared_token = None
        AutoAuthAmountAwareGrowwProvider._shared_auth_session = None
        AutoAuthAmountAwareGrowwProvider._shared_auth_lock = None
        AutoAuthAmountAwareGrowwProvider._auth_blocked_until_monotonic = 0.0

    def test_manual_session_token_avoids_dynamic_generation_even_with_key_pair(self):
        env = {
            "GROWW_API_KEY": "api-key",
            "GROWW_API_SECRET": "api-secret",
            "GROWW_ACCESS_TOKEN": "current-session-token",
        }
        with patch.dict(os.environ, env, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)

        calls = 0

        async def generate():
            nonlocal calls
            calls += 1
            return "generated-token"

        provider._generate_access_token = generate
        first = asyncio.run(provider._get_access_token())
        second = asyncio.run(provider._get_access_token())

        self.assertEqual(first, "current-session-token")
        self.assertEqual(second, "current-session-token")
        self.assertEqual(calls, 0)

    def test_dynamic_credentials_generate_once_without_manual_token(self):
        env = {
            "GROWW_API_KEY": "api-key",
            "GROWW_API_SECRET": "api-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)

        calls = 0

        async def generate():
            nonlocal calls
            calls += 1
            return "generated-token"

        provider._generate_access_token = generate
        first = asyncio.run(provider._get_access_token())
        second = asyncio.run(provider._get_access_token())

        self.assertEqual(first, "generated-token")
        self.assertEqual(second, "generated-token")
        self.assertEqual(calls, 1)

    def test_manual_token_still_works_without_key_pair(self):
        with patch.dict(os.environ, {"GROWW_ACCESS_TOKEN": "manual-token"}, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)

        self.assertEqual(asyncio.run(provider._get_access_token()), "manual-token")

    def test_429_opens_circuit_and_blocks_repeat_generation(self):
        env = {
            "GROWW_API_KEY": "api-key",
            "GROWW_API_SECRET": "api-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)

        calls = 0
        request = httpx.Request("POST", "https://api.groww.in/v1/token/api/access")
        response = httpx.Response(429, request=request)

        async def generate():
            nonlocal calls
            calls += 1
            raise httpx.HTTPStatusError(
                "rate limited",
                request=request,
                response=response,
            )

        provider._generate_access_token = generate

        with self.assertRaises(GrowwAuthRateLimitedError):
            asyncio.run(provider._get_access_token())
        with self.assertRaises(GrowwAuthRateLimitedError):
            asyncio.run(provider._get_access_token())

        self.assertEqual(calls, 1)
        self.assertGreater(
            AutoAuthAmountAwareGrowwProvider._auth_blocked_until_monotonic,
            0.0,
        )

    def test_manual_token_bypasses_open_429_circuit(self):
        AutoAuthAmountAwareGrowwProvider._auth_blocked_until_monotonic = float("inf")
        with patch.dict(os.environ, {"GROWW_ACCESS_TOKEN": "manual-token"}, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)

        self.assertEqual(asyncio.run(provider._get_access_token()), "manual-token")


if __name__ == "__main__":
    unittest.main()
