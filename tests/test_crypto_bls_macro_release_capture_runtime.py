import asyncio
import json
import unittest

from app.crypto_bls_macro_release_capture_runtime import (
    ENV_BLS_ENABLED,
    BlsReleaseRuntimeConfig,
    architecture_contract,
    build_bls_release_runtime,
    initialize_bls_release_runtime,
    run_bls_release_service,
    runtime_status,
)

CPI_URL = "https://www.bls.gov/news.release/cpi.nr0.htm"


class _Store:
    def __init__(self):
        self.initialize_calls = 0

    async def initialize(self):
        self.initialize_calls += 1
        return {"status": "BTC_PIT_POSTGRES_SCHEMA_READY"}

    async def insert_first_seen(self, record):
        raise AssertionError("runtime gating test should not capture a release")


class _HttpClient:
    def __init__(self):
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("runtime build/status/init must not perform BLS request")


def _target_json():
    return json.dumps([{
        "url": CPI_URL,
        "event_type": "CPI",
        "expected_event_key": "BLS:CPI:2026-08",
    }])


class CryptoBlsMacroReleaseCaptureRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_config_is_disabled_and_network_free(self):
        config = BlsReleaseRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.bls_enabled)
        self.assertEqual(config.targets, ())
        runtime = build_bls_release_runtime(config)
        self.assertEqual(runtime["status"], "BLS_RELEASE_RUNTIME_DISABLED")
        self.assertIsNone(runtime["store"])
        self.assertIsNone(runtime["provider"])
        self.assertIsNone(runtime["scheduler"])
        status = runtime_status(config)
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["automatic_target_discovery"])
        self.assertFalse(status["consensus_capture_enabled"])
        self.assertFalse(status["surprise_direction_enabled"])
        self.assertFalse(status["trade_generation_enabled"])

    def test_enabled_bls_requires_archive_database_and_explicit_targets(self):
        target_env = {
            "ALPHAPILOT_CRYPTO_BLS_RELEASES_ENABLED": "true",
            "ALPHAPILOT_CRYPTO_BLS_RELEASE_TARGETS_JSON": _target_json(),
        }
        with self.assertRaises(ValueError):
            BlsReleaseRuntimeConfig.from_env(target_env)

        with self.assertRaises(ValueError):
            BlsReleaseRuntimeConfig.from_env({
                **target_env,
                "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED": "true",
            })

        with self.assertRaises(ValueError):
            BlsReleaseRuntimeConfig.from_env({
                "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED": "true",
                "DATABASE_URL": "postgresql://example/db",
                "ALPHAPILOT_CRYPTO_BLS_RELEASES_ENABLED": "true",
            })

    def test_target_json_is_strictly_validated(self):
        base = {
            "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED": "true",
            "DATABASE_URL": "postgresql://example/db",
            "ALPHAPILOT_CRYPTO_BLS_RELEASES_ENABLED": "true",
        }
        invalid_values = [
            "not-json",
            json.dumps({"url": CPI_URL}),
            json.dumps(["bad"]),
            json.dumps([{"url": CPI_URL, "event_type": "CPI"}]),
            json.dumps([{"url": "https://example.com/cpi", "event_type": "CPI", "expected_event_key": "BLS:CPI:2026-08"}]),
            json.dumps([{"url": CPI_URL, "event_type": "PPI", "expected_event_key": "BLS:PPI:2026-08"}]),
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    BlsReleaseRuntimeConfig.from_env({**base, "ALPHAPILOT_CRYPTO_BLS_RELEASE_TARGETS_JSON": value})

    def test_environment_parses_explicit_target_and_poll_policy(self):
        config = BlsReleaseRuntimeConfig.from_env({
            "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED": "true",
            "DATABASE_URL": "postgresql://example/db",
            "ALPHAPILOT_CRYPTO_BLS_RELEASES_ENABLED": "true",
            "ALPHAPILOT_CRYPTO_BLS_RELEASE_TARGETS_JSON": _target_json(),
            "ALPHAPILOT_CRYPTO_BLS_RELEASE_POLL_SECONDS": "15",
        })
        self.assertTrue(config.archive_enabled)
        self.assertTrue(config.bls_enabled)
        self.assertEqual(config.poll_seconds, 15)
        self.assertEqual(len(config.targets), 1)
        self.assertEqual(config.targets[0].expected_event_key, "BLS:CPI:2026-08")
        with self.assertRaises(ValueError):
            BlsReleaseRuntimeConfig.from_env({"ALPHAPILOT_CRYPTO_BLS_RELEASE_POLL_SECONDS": "bad"})
        with self.assertRaises(ValueError):
            BlsReleaseRuntimeConfig(poll_seconds=9).validated()

    def test_archive_only_runtime_keeps_bls_disabled_without_network(self):
        client = _HttpClient()
        store = _Store()
        config = BlsReleaseRuntimeConfig(
            archive_enabled=True,
            database_url="postgresql://example/db",
            bls_enabled=False,
        )
        runtime = build_bls_release_runtime(config, http_client=client, store=store)
        self.assertEqual(runtime["status"], "BLS_RELEASE_ARCHIVE_ONLY_READY")
        self.assertIs(runtime["store"], store)
        self.assertFalse(runtime["provider"].policy.enabled)
        self.assertFalse(runtime["scheduler"].policy.enabled)
        self.assertEqual(client.calls, [])

    async def test_initialize_only_initializes_schema_and_performs_no_bls_request(self):
        client = _HttpClient()
        store = _Store()
        config = BlsReleaseRuntimeConfig.from_env({
            "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED": "true",
            "DATABASE_URL": "postgresql://example/db",
            "ALPHAPILOT_CRYPTO_BLS_RELEASES_ENABLED": "true",
            "ALPHAPILOT_CRYPTO_BLS_RELEASE_TARGETS_JSON": _target_json(),
        })
        result = await initialize_bls_release_runtime(config, http_client=client, store=store)
        self.assertEqual(result["status"], "BLS_RELEASE_RUNTIME_READY")
        self.assertTrue(result["schema_initialized"])
        self.assertFalse(result["capture_started"])
        self.assertTrue(result["scheduler_enabled"])
        self.assertEqual(result["target_count"], 1)
        self.assertFalse(result["network_request_performed"])
        self.assertEqual(store.initialize_calls, 1)
        self.assertEqual(client.calls, [])

    async def test_disabled_service_does_not_initialize_or_request_bls(self):
        client = _HttpClient()
        store = _Store()
        result = await run_bls_release_service(
            BlsReleaseRuntimeConfig(),
            stop_event=asyncio.Event(),
            http_client=client,
            store=store,
        )
        self.assertEqual(result["status"], "BLS_RELEASE_RUNTIME_DISABLED")
        self.assertEqual(result["cycles"], 0)
        self.assertFalse(result["trade_generated"])
        self.assertEqual(store.initialize_calls, 0)
        self.assertEqual(client.calls, [])

    async def test_enabled_service_with_pre_set_stop_only_initializes_schema(self):
        client = _HttpClient()
        store = _Store()
        stop = asyncio.Event()
        stop.set()
        config = BlsReleaseRuntimeConfig.from_env({
            "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED": "true",
            "DATABASE_URL": "postgresql://example/db",
            "ALPHAPILOT_CRYPTO_BLS_RELEASES_ENABLED": "true",
            "ALPHAPILOT_CRYPTO_BLS_RELEASE_TARGETS_JSON": _target_json(),
        })
        result = await run_bls_release_service(
            config,
            stop_event=stop,
            http_client=client,
            store=store,
        )
        self.assertEqual(result["status"], "BLS_EXACT_RELEASE_CAPTURE_STOPPED")
        self.assertEqual(result["cycles"], 0)
        self.assertFalse(result["trade_generated"])
        self.assertEqual(store.initialize_calls, 1)
        self.assertEqual(client.calls, [])

    def test_architecture_enforces_separate_activation_without_consensus_or_trades(self):
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertEqual(contract["separate_environment_switch"], ENV_BLS_ENABLED)
        self.assertTrue(contract["explicit_target_json_required"])
        self.assertFalse(contract["automatic_target_discovery"])
        self.assertTrue(contract["archive_required_before_capture"])
        self.assertTrue(contract["database_required_before_capture"])
        self.assertFalse(contract["build_performs_network_request"])
        self.assertFalse(contract["status_performs_network_request"])
        self.assertFalse(contract["schema_initialization_starts_capture"])
        self.assertFalse(contract["automatic_startup_registration"])
        self.assertFalse(contract["consensus_provider_enabled_by_bls_runtime"])
        self.assertFalse(contract["surprise_direction_enabled_by_bls_runtime"])
        self.assertFalse(contract["options_trade_generation_enabled"])
        self.assertFalse(contract["futures_trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
