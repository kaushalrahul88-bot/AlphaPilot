import asyncio
import os
import unittest
from unittest.mock import patch

from app.providers.groww_autoauth import AutoAuthAmountAwareGrowwProvider


class GrowwAutoAuthTests(unittest.TestCase):
    def setUp(self):
        AutoAuthAmountAwareGrowwProvider._shared_token = None
        AutoAuthAmountAwareGrowwProvider._shared_auth_session = None
        AutoAuthAmountAwareGrowwProvider._shared_auth_lock = None

    def test_dynamic_credentials_override_stale_manual_token(self):
        env = {
            "GROWW_API_KEY": "api-key",
            "GROWW_API_SECRET": "api-secret",
            "GROWW_ACCESS_TOKEN": "stale-token",
        }
        with patch.dict(os.environ, env, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)

        calls = 0

        async def generate():
            nonlocal calls
            calls += 1
            return "current-session-token"

        provider._generate_access_token = generate
        first = asyncio.run(provider._get_access_token())
        second = asyncio.run(provider._get_access_token())

        self.assertEqual(first, "current-session-token")
        self.assertEqual(second, "current-session-token")
        self.assertEqual(calls, 1)

    def test_manual_token_still_works_without_key_pair(self):
        with patch.dict(os.environ, {"GROWW_ACCESS_TOKEN": "manual-token"}, clear=True):
            provider = AutoAuthAmountAwareGrowwProvider(settings=None)

        self.assertEqual(asyncio.run(provider._get_access_token()), "manual-token")


if __name__ == "__main__":
    unittest.main()
