import asyncio
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

from app import fno_15m_candle_checkpoint_v2 as checkpoint
from app import fno_15m_restart_safe_replay_v2 as replay


class DummyProvider:
    _shared_token = "stale-shared-token"
    _shared_auth_session = "stale-session"

    def __init__(self, *, dynamic="api"):
        self.access_token = "stale-static-token"
        self._cached_token = "stale-cached-token"
        self._cached_auth_session = "stale-session"
        self.api_key = "api-key" if dynamic == "api" else ""
        self.api_secret = "api-secret" if dynamic == "api" else ""
        self.totp_token = "totp-token" if dynamic == "totp" else ""
        self.totp_secret = "totp-secret" if dynamic == "totp" else ""
        self.token_calls = 0

    async def _get_access_token(self):
        self.token_calls += 1
        if self.access_token:
            return self.access_token
        if self.__class__._shared_token is not None:
            return self.__class__._shared_token
        self.__class__._shared_token = "fresh-generated-token"
        self.__class__._shared_auth_session = "fresh-session"
        return "fresh-generated-token"


class FnoHistoricalAuthCheckpointTests(unittest.TestCase):
    def setUp(self):
        DummyProvider._shared_token = "stale-shared-token"
        DummyProvider._shared_auth_session = "stale-session"

    def test_wrapped_401_is_recognized_as_auth_failure(self):
        error = RuntimeError(
            "historical candle fetch failed: Client error '401 Unauthorized' for url"
        )
        self.assertTrue(checkpoint._is_auth_error(error))
        self.assertFalse(checkpoint._is_auth_error(RuntimeError("Groww historical HTTP 500")))

    def test_dynamic_auth_mode_supports_totp_and_api_pair(self):
        self.assertEqual(checkpoint._dynamic_auth_mode(DummyProvider(dynamic="totp")), "TOTP")
        self.assertEqual(checkpoint._dynamic_auth_mode(DummyProvider(dynamic="api")), "API_KEY_SECRET")
        provider = DummyProvider(dynamic="none")
        self.assertIsNone(checkpoint._dynamic_auth_mode(provider))

    def test_refresh_clears_static_and_shared_stale_token_before_generation(self):
        provider = DummyProvider(dynamic="api")
        mode = asyncio.run(checkpoint._refresh_after_401(provider))
        self.assertEqual(mode, "API_KEY_SECRET")
        self.assertEqual(provider.access_token, "")
        self.assertIsNone(provider._cached_token)
        self.assertIsNone(provider._cached_auth_session)
        self.assertEqual(provider.token_calls, 1)
        self.assertEqual(DummyProvider._shared_token, "fresh-generated-token")

    def test_refresh_fails_explicitly_when_no_dynamic_credentials_exist(self):
        provider = DummyProvider(dynamic="none")
        with self.assertRaisesRegex(
            checkpoint.HistoricalAuthenticationError,
            "no complete dynamic Groww credential pair",
        ):
            asyncio.run(checkpoint._refresh_after_401(provider))
        self.assertEqual(provider.access_token, "stale-static-token")
        self.assertEqual(provider.token_calls, 0)

    def test_history_fetch_retries_once_after_401_and_uses_fresh_auth(self):
        provider = DummyProvider(dynamic="totp")
        candle = ["2026-09-01T09:15:00+05:30", 100, 101, 99, 100.5, 10]
        fetch = AsyncMock(
            side_effect=[
                RuntimeError("historical candle fetch failed: 401 Unauthorized"),
                [candle],
            ]
        )
        with patch.object(checkpoint.core, "fetch_historical_candles", fetch):
            result = asyncio.run(
                checkpoint._fetch_one_history(
                    provider,
                    "NIFTY",
                    "5m",
                    datetime(2026, 8, 25, tzinfo=timezone.utc),
                    datetime(2026, 9, 4, tzinfo=timezone.utc),
                )
            )
        self.assertEqual(result, [candle])
        self.assertEqual(fetch.await_count, 2)
        self.assertEqual(provider.token_calls, 1)
        self.assertEqual(provider.access_token, "")

    def test_401_after_refresh_fails_closed(self):
        provider = DummyProvider(dynamic="api")
        fetch = AsyncMock(
            side_effect=[
                RuntimeError("historical candle fetch failed: 401 Unauthorized"),
                RuntimeError("historical candle fetch failed: 401 Unauthorized"),
            ]
        )
        with patch.object(checkpoint.core, "fetch_historical_candles", fetch):
            with self.assertRaisesRegex(
                checkpoint.HistoricalAuthenticationError,
                "401_AFTER_REFRESH",
            ):
                asyncio.run(
                    checkpoint._fetch_one_history(
                        provider,
                        "NIFTY",
                        "5m",
                        datetime(2026, 8, 25, tzinfo=timezone.utc),
                        datetime(2026, 9, 4, tzinfo=timezone.utc),
                    )
                )
        self.assertEqual(fetch.await_count, 2)

    def test_cached_failure_is_retried_and_replaced_by_success(self):
        provider = DummyProvider(dynamic="api")
        candle = ["2026-09-01T09:15:00+05:30", 100, 101, 99, 100.5, 10]
        ensure = AsyncMock()
        load = AsyncMock(return_value=([], "RuntimeError: 401 Unauthorized"))
        fetch = AsyncMock(return_value=[candle])
        save = AsyncMock()
        with (
            patch.object(checkpoint, "ensure_cache_schema", ensure),
            patch.object(checkpoint, "load_cached_history", load),
            patch.object(checkpoint, "_fetch_one_history", fetch),
            patch.object(checkpoint, "save_cached_history", save),
            patch.object(checkpoint, "dataset_key", return_value="dataset"),
            patch.object(checkpoint.core, "TIMEFRAMES", ("5m",)),
            patch.object(checkpoint.core, "LOOKBACK_DAYS", {"5m": 7}),
        ):
            histories, failures, key = asyncio.run(
                checkpoint.fetch_all_histories_checkpointed_v2(
                    provider,
                    ["NIFTY"],
                    [date(2026, 9, 1)],
                    "postgresql://unused",
                )
            )
        self.assertEqual(key, "dataset")
        self.assertEqual(histories, {"NIFTY": {"5m": [candle]}})
        self.assertEqual(failures, [])
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(save.await_count, 1)
        saved_args = save.await_args.args
        self.assertEqual(saved_args[-1], None)

    def test_auth_failure_is_not_saved_as_empty_cache_entry(self):
        provider = DummyProvider(dynamic="api")
        ensure = AsyncMock()
        load = AsyncMock(return_value=None)
        fetch = AsyncMock(
            side_effect=checkpoint.HistoricalAuthenticationError("blocked auth")
        )
        save = AsyncMock()
        with (
            patch.object(checkpoint, "ensure_cache_schema", ensure),
            patch.object(checkpoint, "load_cached_history", load),
            patch.object(checkpoint, "_fetch_one_history", fetch),
            patch.object(checkpoint, "save_cached_history", save),
            patch.object(checkpoint, "dataset_key", return_value="dataset"),
            patch.object(checkpoint.core, "TIMEFRAMES", ("5m",)),
            patch.object(checkpoint.core, "LOOKBACK_DAYS", {"5m": 7}),
        ):
            with self.assertRaises(checkpoint.HistoricalAuthenticationError):
                asyncio.run(
                    checkpoint.fetch_all_histories_checkpointed_v2(
                        provider,
                        ["NIFTY"],
                        [date(2026, 9, 1)],
                        "postgresql://unused",
                    )
                )
        self.assertEqual(save.await_count, 0)

    def test_day_bounded_replay_is_wired_to_auth_safe_checkpoint_loader(self):
        self.assertIs(
            replay.fetch_all_histories_checkpointed_v2,
            checkpoint.fetch_all_histories_checkpointed_v2,
        )
        contract = replay.architecture_contract()
        self.assertTrue(contract["historical_auth_401_fail_closed"])
        self.assertTrue(contract["cached_candle_errors_retried"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
