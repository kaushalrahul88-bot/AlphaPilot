import asyncio
import json
import unittest
from datetime import datetime, timezone

from app.crypto_macro_live_availability_runtime import (
    ENV_AUDIT_ENABLED,
    ENV_DATABASE_URL,
    ENV_MASSIVE_API_KEY,
    ENV_MAX_LATENCY_SECONDS,
    ENV_MIN_UNIQUE_EVENTS,
    ENV_POLL_SECONDS,
    ENV_STORE_ENABLED,
    ENV_TARGETS_JSON,
    MacroLiveAvailabilityRuntimeConfig,
    architecture_contract,
    build_macro_live_availability_runtime,
    initialize_macro_live_availability_runtime,
    run_macro_live_availability_service,
    runtime_status,
)


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
TARGETS = json.dumps([
    {
        "event_key": "BLS:CPI:2026-08",
        "event_type": "CPI",
        "release_at": RELEASE.isoformat(),
    }
])


class _FakeStore:
    def __init__(self):
        self.initialize_calls = 0

    async def initialize(self):
        self.initialize_calls += 1
        return {"status": "MACRO_LIVE_AVAILABILITY_POSTGRES_SCHEMA_READY"}

    def list_attempts(self):
        return []

    def insert_attempt(self, attempt):
        return {"status": "INSERTED_AVAILABILITY_ATTEMPT"}


class CryptoMacroLiveAvailabilityRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_environment_is_fully_disabled_and_network_free(self):
        config = MacroLiveAvailabilityRuntimeConfig.from_env({})
        self.assertFalse(config.store_enabled)
        self.assertFalse(config.audit_enabled)
        runtime = build_macro_live_availability_runtime(config)
        self.assertEqual(runtime["status"], "MACRO_LIVE_AVAILABILITY_RUNTIME_DISABLED")
        self.assertIsNone(runtime["store"])
        self.assertIsNone(runtime["provider"])
        self.assertIsNone(runtime["scheduler"])

    def test_store_only_mode_does_not_enable_provider_network_audit(self):
        store = _FakeStore()
        config = MacroLiveAvailabilityRuntimeConfig.from_env({
            ENV_STORE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example",
        })
        runtime = build_macro_live_availability_runtime(config, store=store)
        self.assertEqual(runtime["status"], "MACRO_LIVE_AVAILABILITY_STORE_ONLY_READY")
        self.assertFalse(runtime["provider"].policy.enabled)
        self.assertFalse(runtime["scheduler"].policy.enabled)

    def test_audit_requires_store_database_api_key_and_explicit_targets(self):
        with self.assertRaisesRegex(ValueError, "Postgres store"):
            MacroLiveAvailabilityRuntimeConfig(
                audit_enabled=True,
                massive_api_key="KEY",
                targets=MacroLiveAvailabilityRuntimeConfig.from_env({ENV_TARGETS_JSON: TARGETS}).targets,
            ).validated()
        with self.assertRaisesRegex(ValueError, "api_key"):
            MacroLiveAvailabilityRuntimeConfig.from_env({
                ENV_STORE_ENABLED: "true",
                ENV_DATABASE_URL: "postgresql://example",
                ENV_AUDIT_ENABLED: "true",
                ENV_TARGETS_JSON: TARGETS,
            })
        with self.assertRaisesRegex(ValueError, "no explicit targets"):
            MacroLiveAvailabilityRuntimeConfig.from_env({
                ENV_STORE_ENABLED: "true",
                ENV_DATABASE_URL: "postgresql://example",
                ENV_AUDIT_ENABLED: "true",
                ENV_MASSIVE_API_KEY: "KEY",
            })

    def test_explicit_runtime_config_is_independent_and_bounded(self):
        config = MacroLiveAvailabilityRuntimeConfig.from_env({
            ENV_STORE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example",
            ENV_AUDIT_ENABLED: "true",
            ENV_MASSIVE_API_KEY: "KEY",
            ENV_TARGETS_JSON: TARGETS,
            ENV_POLL_SECONDS: "20",
            ENV_MAX_LATENCY_SECONDS: "90",
            ENV_MIN_UNIQUE_EVENTS: "4",
        })
        self.assertTrue(config.store_enabled)
        self.assertTrue(config.audit_enabled)
        self.assertEqual(config.poll_seconds, 20)
        self.assertEqual(config.max_latency_seconds, 90.0)
        self.assertEqual(config.min_unique_events, 4)
        self.assertEqual(len(config.targets), 1)
        runtime = build_macro_live_availability_runtime(config, store=_FakeStore())
        self.assertEqual(runtime["status"], "MACRO_LIVE_AVAILABILITY_RUNTIME_READY")
        self.assertTrue(runtime["provider"].policy.enabled)
        self.assertTrue(runtime["scheduler"].policy.enabled)
        self.assertEqual(runtime["provider"].policy.reaction_window_minutes, 10)
        self.assertEqual(runtime["provider"].policy.selection_window_minutes, 30)

    async def test_initialize_only_creates_schema_and_never_starts_audit(self):
        store = _FakeStore()
        config = MacroLiveAvailabilityRuntimeConfig.from_env({
            ENV_STORE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example",
            ENV_AUDIT_ENABLED: "true",
            ENV_MASSIVE_API_KEY: "KEY",
            ENV_TARGETS_JSON: TARGETS,
        })
        result = await initialize_macro_live_availability_runtime(config, store=store)
        self.assertTrue(result["schema_initialized"])
        self.assertFalse(result["audit_started"])
        self.assertFalse(result["network_request_performed"])
        self.assertFalse(result["live_confirmation_enabled"])
        self.assertEqual(store.initialize_calls, 1)

    async def test_pre_stopped_service_initializes_store_but_performs_no_audit_cycle(self):
        store = _FakeStore()
        config = MacroLiveAvailabilityRuntimeConfig.from_env({
            ENV_STORE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example",
            ENV_AUDIT_ENABLED: "true",
            ENV_MASSIVE_API_KEY: "KEY",
            ENV_TARGETS_JSON: TARGETS,
        })
        stop = asyncio.Event()
        stop.set()
        result = await run_macro_live_availability_service(config, stop_event=stop, store=store)
        self.assertEqual(result["status"], "MACRO_LIVE_AVAILABILITY_AUDIT_STOPPED")
        self.assertEqual(result["cycles"], 0)
        self.assertFalse(result["live_confirmation_enabled"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(store.initialize_calls, 1)

    def test_runtime_status_never_exposes_api_key_or_claims_realtime(self):
        config = MacroLiveAvailabilityRuntimeConfig.from_env({
            ENV_STORE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example",
            ENV_AUDIT_ENABLED: "true",
            ENV_MASSIVE_API_KEY: "SECRET-KEY",
            ENV_TARGETS_JSON: TARGETS,
        })
        status = runtime_status(config)
        self.assertTrue(status["api_key_configured"])
        self.assertNotIn("massive_api_key", status)
        self.assertNotIn("SECRET-KEY", repr(status))
        self.assertFalse(status["provider_plan_label_trusted_as_realtime"])
        self.assertFalse(status["historical_reconstruction_trusted_as_live_proof"])
        self.assertFalse(status["automatic_startup_registration"])
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["live_confirmation_enabled"])

    def test_existing_crypto_switches_cannot_activate_this_runtime(self):
        config = MacroLiveAvailabilityRuntimeConfig.from_env({
            "ALPHAPILOT_CRYPTO_BLS_MACRO_RELEASE_ENABLED": "true",
            "ALPHAPILOT_CRYPTO_FRED_MACRO_ENABLED": "true",
            "ALPHAPILOT_CRYPTO_TRADING_ECONOMICS_CONSENSUS_ENABLED": "true",
            "ALPHAPILOT_CRYPTO_NEWS_ENABLED": "true",
            "ALPHAPILOT_CRYPTO_BTC_CAPTURE_ENABLED": "true",
            ENV_DATABASE_URL: "postgresql://example",
            ENV_MASSIVE_API_KEY: "KEY",
            ENV_TARGETS_JSON: TARGETS,
        })
        self.assertFalse(config.store_enabled)
        self.assertFalse(config.audit_enabled)

    def test_target_json_and_numeric_settings_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "exactly"):
            MacroLiveAvailabilityRuntimeConfig.from_env({
                ENV_TARGETS_JSON: json.dumps([{
                    "event_key": "BLS:CPI:2026-08",
                    "event_type": "CPI",
                    "release_at": RELEASE.isoformat(),
                    "extra": True,
                }])
            })
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            MacroLiveAvailabilityRuntimeConfig.from_env({
                ENV_TARGETS_JSON: json.dumps([{
                    "event_key": "BLS:CPI:2026-08",
                    "event_type": "CPI",
                    "release_at": "2026-09-11T12:30:00",
                }])
            })
        with self.assertRaises(ValueError):
            MacroLiveAvailabilityRuntimeConfig.from_env({ENV_POLL_SECONDS: "abc"})
        with self.assertRaises(ValueError):
            MacroLiveAvailabilityRuntimeConfig.from_env({ENV_MAX_LATENCY_SECONDS: "600"})

    def test_architecture_requires_separate_opt_in_and_never_enables_trading(self):
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertEqual(contract["separate_store_switch"], ENV_STORE_ENABLED)
        self.assertEqual(contract["separate_audit_switch"], ENV_AUDIT_ENABLED)
        self.assertTrue(contract["explicit_target_json_required"])
        self.assertFalse(contract["automatic_target_discovery"])
        self.assertTrue(contract["database_required_before_audit"])
        self.assertTrue(contract["store_required_before_audit"])
        self.assertTrue(contract["api_key_required_before_audit"])
        self.assertFalse(contract["build_performs_network_request"])
        self.assertFalse(contract["status_performs_network_request"])
        self.assertFalse(contract["schema_initialization_starts_audit"])
        self.assertFalse(contract["automatic_startup_registration"])
        self.assertFalse(contract["provider_plan_label_trusted_as_realtime"])
        self.assertFalse(contract["historical_reconstruction_trusted_as_live_proof"])
        self.assertFalse(contract["bls_runtime_enables_audit"])
        self.assertFalse(contract["fred_runtime_enables_audit"])
        self.assertFalse(contract["tradingeconomics_runtime_enables_audit"])
        self.assertFalse(contract["qualification_auto_enables_live_confirmation"])
        self.assertFalse(contract["live_confirmation_enabled"])
        self.assertFalse(contract["direction_generation_enabled"])
        self.assertFalse(contract["options_trade_generation_enabled"])
        self.assertFalse(contract["futures_trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
