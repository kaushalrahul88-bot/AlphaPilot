import json
import unittest
from datetime import datetime, timezone

from app.crypto_tradingeconomics_consensus_capture_runtime import (
    ENV_ARCHIVE_ENABLED,
    ENV_DATABASE_URL,
    ENV_TE_API_KEY,
    ENV_TE_ENABLED,
    ENV_TE_POLL_SECONDS,
    ENV_TE_TARGETS_JSON,
    TradingEconomicsConsensusRuntimeConfig,
    architecture_contract,
    build_tradingeconomics_consensus_runtime,
    initialize_tradingeconomics_consensus_runtime,
    runtime_status,
)


TARGET = {
    "event_key": "BLS:CPI:2026-08",
    "event_type": "CPI",
    "reference_period": "2026-08",
    "expected_release_at": "2026-09-11T12:30:00Z",
}


class _Store:
    def __init__(self):
        self.initialize_calls = 0

    async def initialize(self):
        self.initialize_calls += 1
        return {"status": "BTC_PIT_POSTGRES_SCHEMA_READY"}


class _NeverHttpClient:
    def __init__(self):
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("network must not be called during runtime build/status/schema initialization")


class TradingEconomicsConsensusRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_config_is_disabled(self):
        config = TradingEconomicsConsensusRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.consensus_enabled)
        self.assertEqual(config.targets, ())
        self.assertEqual(config.poll_seconds, 300)
        runtime = build_tradingeconomics_consensus_runtime(config)
        self.assertEqual(runtime["status"], "TRADING_ECONOMICS_CONSENSUS_RUNTIME_DISABLED")
        self.assertIsNone(runtime["provider"])
        self.assertIsNone(runtime["scheduler"])

    def test_enabled_capture_requires_archive_database_api_key_and_targets(self):
        base = {
            ENV_TE_ENABLED: "true",
            ENV_TE_API_KEY: "KEY",
            ENV_TE_TARGETS_JSON: json.dumps([TARGET]),
        }
        with self.assertRaisesRegex(ValueError, "immutable PIT archive"):
            TradingEconomicsConsensusRuntimeConfig.from_env(base)

        no_db = dict(base)
        no_db[ENV_ARCHIVE_ENABLED] = "true"
        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            TradingEconomicsConsensusRuntimeConfig.from_env(no_db)

        no_key = {
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgres://example",
            ENV_TE_ENABLED: "true",
            ENV_TE_TARGETS_JSON: json.dumps([TARGET]),
        }
        with self.assertRaisesRegex(ValueError, "api_key"):
            TradingEconomicsConsensusRuntimeConfig.from_env(no_key)

        no_targets = {
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgres://example",
            ENV_TE_ENABLED: "true",
            ENV_TE_API_KEY: "KEY",
        }
        with self.assertRaisesRegex(ValueError, "no explicit targets"):
            TradingEconomicsConsensusRuntimeConfig.from_env(no_targets)

    def test_archive_only_does_not_enable_consensus(self):
        config = TradingEconomicsConsensusRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgres://example",
        })
        runtime = build_tradingeconomics_consensus_runtime(config, store=_Store())
        self.assertEqual(runtime["status"], "TRADING_ECONOMICS_CONSENSUS_ARCHIVE_ONLY_READY")
        self.assertFalse(runtime["provider"].policy.enabled)
        self.assertFalse(runtime["scheduler"].policy.enabled)

    def test_enabled_runtime_builds_with_explicit_target_and_independent_switch(self):
        config = TradingEconomicsConsensusRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgres://example",
            ENV_TE_ENABLED: "true",
            ENV_TE_API_KEY: "KEY",
            ENV_TE_TARGETS_JSON: json.dumps([TARGET]),
            ENV_TE_POLL_SECONDS: "600",
        })
        runtime = build_tradingeconomics_consensus_runtime(config, store=_Store())
        self.assertEqual(runtime["status"], "TRADING_ECONOMICS_CONSENSUS_RUNTIME_READY")
        self.assertTrue(runtime["provider"].policy.enabled)
        self.assertTrue(runtime["scheduler"].policy.enabled)
        self.assertEqual(runtime["scheduler"].policy.poll_seconds, 600)
        self.assertEqual(runtime["scheduler"].targets[0].event_key, TARGET["event_key"])

    async def test_schema_initialization_is_network_free_and_does_not_start_capture(self):
        client = _NeverHttpClient()
        store = _Store()
        config = TradingEconomicsConsensusRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgres://example",
            ENV_TE_ENABLED: "true",
            ENV_TE_API_KEY: "KEY",
            ENV_TE_TARGETS_JSON: json.dumps([TARGET]),
        })
        result = await initialize_tradingeconomics_consensus_runtime(
            config,
            http_client=client,
            clock=lambda: datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
            store=store,
        )
        self.assertTrue(result["schema_initialized"])
        self.assertFalse(result["capture_started"])
        self.assertFalse(result["network_request_performed"])
        self.assertEqual(result["target_count"], 1)
        self.assertEqual(client.calls, [])
        self.assertEqual(store.initialize_calls, 1)

    def test_target_json_is_strict_and_release_timestamp_must_be_aware(self):
        env = {
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgres://example",
            ENV_TE_ENABLED: "true",
            ENV_TE_API_KEY: "KEY",
        }
        malformed = dict(env)
        malformed[ENV_TE_TARGETS_JSON] = "{}"
        with self.assertRaisesRegex(ValueError, "must be a list"):
            TradingEconomicsConsensusRuntimeConfig.from_env(malformed)

        extra = dict(TARGET)
        extra["unexpected"] = True
        bad_keys = dict(env)
        bad_keys[ENV_TE_TARGETS_JSON] = json.dumps([extra])
        with self.assertRaisesRegex(ValueError, "contain exactly"):
            TradingEconomicsConsensusRuntimeConfig.from_env(bad_keys)

        naive = dict(TARGET)
        naive["expected_release_at"] = "2026-09-11T12:30:00"
        naive_env = dict(env)
        naive_env[ENV_TE_TARGETS_JSON] = json.dumps([naive])
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            TradingEconomicsConsensusRuntimeConfig.from_env(naive_env)

    def test_duplicate_targets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            TradingEconomicsConsensusRuntimeConfig.from_env({
                ENV_ARCHIVE_ENABLED: "true",
                ENV_DATABASE_URL: "postgres://example",
                ENV_TE_ENABLED: "true",
                ENV_TE_API_KEY: "KEY",
                ENV_TE_TARGETS_JSON: json.dumps([TARGET, TARGET]),
            })

    def test_runtime_status_never_exposes_api_key_or_enables_other_macro_sources(self):
        config = TradingEconomicsConsensusRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgres://example",
            ENV_TE_ENABLED: "true",
            ENV_TE_API_KEY: "SECRET",
            ENV_TE_TARGETS_JSON: json.dumps([TARGET]),
        })
        status = runtime_status(config)
        self.assertTrue(status["api_key_configured"])
        self.assertNotIn("api_key", {key for key in status if key != "api_key_configured"})
        self.assertFalse(status["bls_release_capture_enabled"])
        self.assertFalse(status["fred_macro_capture_enabled"])
        self.assertFalse(status["numeric_surprise_enabled"])
        self.assertFalse(status["trade_generation_enabled"])

    def test_architecture_requires_separate_activation_and_never_generates_trade(self):
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertTrue(contract["explicit_target_json_required"])
        self.assertTrue(contract["official_release_timestamp_required_in_target"])
        self.assertTrue(contract["archive_required_before_capture"])
        self.assertTrue(contract["api_key_required_before_capture"])
        self.assertFalse(contract["build_performs_network_request"])
        self.assertFalse(contract["schema_initialization_starts_capture"])
        self.assertFalse(contract["bls_runtime_enables_consensus"])
        self.assertFalse(contract["fred_runtime_enables_consensus"])
        self.assertFalse(contract["numeric_surprise_enabled_by_runtime"])
        self.assertFalse(contract["options_trade_generation_enabled"])
        self.assertFalse(contract["futures_trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
