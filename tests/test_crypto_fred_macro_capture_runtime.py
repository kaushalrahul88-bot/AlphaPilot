import asyncio
import unittest

from app.crypto_fred_macro_capture_runtime import (
    ENV_FRED_MACRO_ENABLED,
    FredMacroRuntimeConfig,
    architecture_contract,
    build_fred_macro_runtime,
    initialize_fred_macro_runtime,
    run_fred_macro_service,
    runtime_status,
)


class _Store:
    def __init__(self):
        self.initialize_calls = 0

    async def initialize(self):
        self.initialize_calls += 1
        return {"status": "BTC_PIT_POSTGRES_SCHEMA_READY"}

    async def insert_first_seen(self, record):
        raise AssertionError("runtime gating test should not capture")


class _HttpClient:
    def __init__(self):
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("runtime build/status/init must not perform FRED request")


class CryptoFredMacroCaptureRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_config_is_disabled_and_network_free(self):
        config = FredMacroRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.fred_enabled)
        runtime = build_fred_macro_runtime(config)
        self.assertEqual(runtime["status"], "FRED_MACRO_RUNTIME_DISABLED")
        self.assertIsNone(runtime["store"])
        self.assertIsNone(runtime["provider"])
        self.assertIsNone(runtime["scheduler"])
        status = runtime_status(config)
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["trade_generation_enabled"])

    def test_enabled_fred_requires_api_key_archive_and_database(self):
        with self.assertRaises(ValueError):
            FredMacroRuntimeConfig(
                archive_enabled=False,
                database_url="postgresql://example/db",
                fred_enabled=True,
                api_key="a" * 32,
            ).validated()
        with self.assertRaises(ValueError):
            FredMacroRuntimeConfig(
                archive_enabled=True,
                database_url="",
                fred_enabled=True,
                api_key="a" * 32,
            ).validated()
        with self.assertRaises(ValueError):
            FredMacroRuntimeConfig(
                archive_enabled=True,
                database_url="postgresql://example/db",
                fred_enabled=True,
                api_key="",
            ).validated()

    def test_environment_parsing_and_policy_validation_fail_closed(self):
        config = FredMacroRuntimeConfig.from_env({
            "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED": "true",
            "DATABASE_URL": "postgresql://example/db",
            "ALPHAPILOT_CRYPTO_FRED_MACRO_ENABLED": "true",
            "ALPHAPILOT_CRYPTO_FRED_API_KEY": "a" * 32,
            "ALPHAPILOT_CRYPTO_FRED_MACRO_POLL_SECONDS": "1800",
            "ALPHAPILOT_CRYPTO_FRED_MACRO_LOOKBACK_DAYS": "60",
        })
        self.assertTrue(config.archive_enabled)
        self.assertTrue(config.fred_enabled)
        self.assertEqual(config.poll_seconds, 1800)
        self.assertEqual(config.lookback_days, 60)
        with self.assertRaises(ValueError):
            FredMacroRuntimeConfig.from_env({"ALPHAPILOT_CRYPTO_FRED_MACRO_POLL_SECONDS": "bad"})
        with self.assertRaises(ValueError):
            FredMacroRuntimeConfig(poll_seconds=899).validated()
        with self.assertRaises(ValueError):
            FredMacroRuntimeConfig(lookback_days=6).validated()

    def test_archive_only_runtime_keeps_provider_and_scheduler_disabled_without_network(self):
        client = _HttpClient()
        store = _Store()
        config = FredMacroRuntimeConfig(
            archive_enabled=True,
            database_url="postgresql://example/db",
            fred_enabled=False,
        )
        runtime = build_fred_macro_runtime(config, http_client=client, store=store)
        self.assertEqual(runtime["status"], "FRED_MACRO_ARCHIVE_ONLY_READY")
        self.assertIs(runtime["store"], store)
        self.assertFalse(runtime["provider"].policy.enabled)
        self.assertFalse(runtime["scheduler"].policy.enabled)
        self.assertEqual(client.calls, [])

    async def test_initialize_only_initializes_schema_and_performs_no_fred_request(self):
        client = _HttpClient()
        store = _Store()
        config = FredMacroRuntimeConfig(
            archive_enabled=True,
            database_url="postgresql://example/db",
            fred_enabled=True,
            api_key="a" * 32,
        )
        result = await initialize_fred_macro_runtime(config, http_client=client, store=store)
        self.assertEqual(result["status"], "FRED_MACRO_RUNTIME_READY")
        self.assertTrue(result["schema_initialized"])
        self.assertFalse(result["capture_started"])
        self.assertTrue(result["scheduler_enabled"])
        self.assertFalse(result["network_request_performed"])
        self.assertEqual(store.initialize_calls, 1)
        self.assertEqual(client.calls, [])

    async def test_disabled_service_does_not_initialize_or_call_provider(self):
        client = _HttpClient()
        store = _Store()
        result = await run_fred_macro_service(
            FredMacroRuntimeConfig(),
            stop_event=asyncio.Event(),
            http_client=client,
            store=store,
        )
        self.assertEqual(result["status"], "FRED_MACRO_RUNTIME_DISABLED")
        self.assertEqual(result["cycles"], 0)
        self.assertFalse(result["trade_generated"])
        self.assertEqual(store.initialize_calls, 0)
        self.assertEqual(client.calls, [])

    async def test_enabled_service_with_pre_set_stop_only_initializes_schema(self):
        client = _HttpClient()
        store = _Store()
        stop = asyncio.Event()
        stop.set()
        config = FredMacroRuntimeConfig(
            archive_enabled=True,
            database_url="postgresql://example/db",
            fred_enabled=True,
            api_key="a" * 32,
        )
        result = await run_fred_macro_service(
            config,
            stop_event=stop,
            http_client=client,
            store=store,
        )
        self.assertEqual(result["status"], "FRED_MACRO_CAPTURE_STOPPED")
        self.assertEqual(result["cycles"], 0)
        self.assertFalse(result["trade_generated"])
        self.assertEqual(store.initialize_calls, 1)
        self.assertEqual(client.calls, [])

    def test_architecture_enforces_separate_switch_and_no_trade_activation(self):
        contract = architecture_contract()
        self.assertFalse(contract["fred_enabled_by_default"])
        self.assertEqual(contract["separate_environment_switch"], ENV_FRED_MACRO_ENABLED)
        self.assertTrue(contract["api_key_required_before_capture"])
        self.assertTrue(contract["archive_required_before_capture"])
        self.assertTrue(contract["database_required_before_capture"])
        self.assertFalse(contract["build_performs_network_request"])
        self.assertFalse(contract["status_performs_network_request"])
        self.assertFalse(contract["schema_initialization_starts_capture"])
        self.assertFalse(contract["historical_reconstruction_started_by_live_runtime"])
        self.assertFalse(contract["news_derivatives_onchain_or_stablecoin_switch_enables_fred"])
        self.assertFalse(contract["daily_regime_may_supply_second_intraday_origin"])
        self.assertFalse(contract["options_trade_generation_enabled"])
        self.assertFalse(contract["futures_trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
