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
        self.assertEqual(asyncio.run(provider._get_access_token()), "current-session-token")
        self.assertEqual(asyncio.run(provider._get_access_token()), "current-session-token")
        self.assertEqual(calls, 0)

    def test_dynamic_credentials_generate_once_without_manual_token(self):
        with patch.dict(os.environ, {"GROWW_API_KEY": "api-key", "GROWW_API_SECRET": "api-secret"}, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)
        calls = 0

        async def generate():
            nonlocal calls
            calls += 1
            return "generated-token"

        provider._generate_access_token = generate
        self.assertEqual(asyncio.run(provider._get_access_token()), "generated-token")
        self.assertEqual(asyncio.run(provider._get_access_token()), "generated-token")
        self.assertEqual(calls, 1)

    def test_manual_token_still_works_without_key_pair(self):
        with patch.dict(os.environ, {"GROWW_ACCESS_TOKEN": "manual-token"}, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)
        self.assertEqual(asyncio.run(provider._get_access_token()), "manual-token")

    def test_totp_credentials_work_without_legacy_key_pair(self):
        env = {"GROWW_TOTP_TOKEN": "totp-token", "GROWW_TOTP_SECRET": "JBSWY3DPEHPK3PXP"}
        with patch.dict(os.environ, env, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)
        self.assertEqual(provider.totp_token, "totp-token")
        self.assertEqual(provider.totp_secret, "JBSWY3DPEHPK3PXP")

    def test_totp_generation_matches_rfc6238_vector_shape(self):
        code = AutoAuthAmountAwareGrowwProvider._totp_now("JBSWY3DPEHPK3PXP", now=59)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertEqual(code, AutoAuthAmountAwareGrowwProvider._totp_now("JBSWY3DPEHPK3PXP", now=59))

    def test_totp_is_preferred_over_legacy_approval_flow(self):
        env = {
            "GROWW_TOTP_TOKEN": "totp-token",
            "GROWW_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
            "GROWW_API_KEY": "legacy-key",
            "GROWW_API_SECRET": "legacy-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)
        captured = {}

        async def post_access_token(*, api_key, payload):
            captured["api_key"] = api_key
            captured["payload"] = payload
            return "session-token"

        provider._post_access_token = post_access_token
        self.assertEqual(asyncio.run(provider._generate_access_token()), "session-token")
        self.assertEqual(captured["api_key"], "totp-token")
        self.assertEqual(captured["payload"]["key_type"], "totp")
        self.assertEqual(len(captured["payload"]["totp"]), 6)
        self.assertNotIn("checksum", captured["payload"])

    def test_429_opens_circuit_and_blocks_repeat_generation(self):
        env = {"GROWW_API_KEY": "api-key", "GROWW_API_SECRET": "api-secret"}
        with patch.dict(os.environ, env, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)
        calls = 0
        request = httpx.Request("POST", "https://api.groww.in/v1/token/api/access")
        response = httpx.Response(429, request=request)

        async def generate():
            nonlocal calls
            calls += 1
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

        provider._generate_access_token = generate
        with self.assertRaises(GrowwAuthRateLimitedError):
            asyncio.run(provider._get_access_token())
        with self.assertRaises(GrowwAuthRateLimitedError):
            asyncio.run(provider._get_access_token())
        self.assertEqual(calls, 1)
        self.assertGreater(AutoAuthAmountAwareGrowwProvider._auth_blocked_until_monotonic, 0.0)

    def test_manual_token_bypasses_open_429_circuit(self):
        AutoAuthAmountAwareGrowwProvider._auth_blocked_until_monotonic = float("inf")
        with patch.dict(os.environ, {"GROWW_ACCESS_TOKEN": "manual-token"}, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)
        self.assertEqual(asyncio.run(provider._get_access_token()), "manual-token")


if __name__ == "__main__":
    unittest.main()
