import unittest

from app.crypto_stablecoin_capture_runtime import (
    ENV_ARCHIVE_ENABLED,
    ENV_DATABASE_URL,
    ENV_STABLECOIN_ENABLED,
    ENV_STABLECOIN_PEG_TYPE,
    ENV_STABLECOIN_POLL_SECONDS,
    StablecoinSupplyRuntimeConfig,
    architecture_contract,
    build_stablecoin_supply_runtime,
    runtime_status,
)


class CryptoStablecoinCaptureRuntimeTests(unittest.TestCase):
    def test_default_environment_disables_stablecoin_capture(self):
        config = StablecoinSupplyRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.stablecoin_enabled)
        runtime = build_stablecoin_supply_runtime(config)
        self.assertEqual(runtime["status"], "STABLECOIN_SUPPLY_RUNTIME_DISABLED")
        self.assertIsNone(runtime["provider"])
        self.assertIsNone(runtime["scheduler"])

    def test_stablecoin_capture_cannot_run_without_archive(self):
        with self.assertRaises(ValueError):
            StablecoinSupplyRuntimeConfig.from_env({
                ENV_STABLECOIN_ENABLED: "true",
                ENV_DATABASE_URL: "postgresql://example/test",
            })

    def test_archive_enabled_requires_database(self):
        with self.assertRaises(ValueError):
            StablecoinSupplyRuntimeConfig.from_env({ENV_ARCHIVE_ENABLED: "true"})

    def test_archive_only_mode_keeps_provider_and_scheduler_disabled(self):
        config = StablecoinSupplyRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
        })
        runtime = build_stablecoin_supply_runtime(config)
        self.assertEqual(runtime["status"], "STABLECOIN_SUPPLY_ARCHIVE_ONLY_READY")
        self.assertFalse(runtime["provider"].policy.enabled)
        self.assertFalse(runtime["scheduler"].policy.enabled)

    def test_explicit_stablecoin_config_is_separate_and_configurable(self):
        config = StablecoinSupplyRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
            ENV_STABLECOIN_ENABLED: "true",
            ENV_STABLECOIN_POLL_SECONDS: "900",
            ENV_STABLECOIN_PEG_TYPE: "peggedUSD",
        })
        runtime = build_stablecoin_supply_runtime(config)
        self.assertEqual(runtime["status"], "STABLECOIN_SUPPLY_RUNTIME_READY")
        self.assertTrue(runtime["provider"].policy.enabled)
        self.assertTrue(runtime["scheduler"].policy.enabled)
        self.assertEqual(runtime["scheduler"].policy.poll_seconds, 900)
        self.assertEqual(runtime["provider"].policy.peg_type, "peggedUSD")

    def test_status_is_descriptive_without_network(self):
        status = runtime_status(StablecoinSupplyRuntimeConfig())
        self.assertFalse(status["stablecoin_enabled"])
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["automatic_startup_registration"])
        self.assertFalse(status["trade_generation_enabled"])

    def test_invalid_values_fail_closed(self):
        with self.assertRaises(ValueError):
            StablecoinSupplyRuntimeConfig.from_env({ENV_STABLECOIN_ENABLED: "maybe"})
        with self.assertRaises(ValueError):
            StablecoinSupplyRuntimeConfig.from_env({ENV_STABLECOIN_POLL_SECONDS: "299"})
        with self.assertRaises(ValueError):
            StablecoinSupplyRuntimeConfig.from_env({ENV_STABLECOIN_PEG_TYPE: ""})

    def test_contract_prevents_other_capture_switches_from_enabling_stablecoin(self):
        contract = architecture_contract()
        self.assertFalse(contract["stablecoin_enabled_by_default"])
        self.assertFalse(contract["api_key_required"])
        self.assertTrue(contract["archive_required_before_capture"])
        self.assertTrue(contract["database_required_before_capture"])
        self.assertFalse(contract["derivatives_news_or_onchain_switch_enables_stablecoin"])
        self.assertFalse(contract["automatic_startup_registration"])
        self.assertFalse(contract["automatic_network_request"])
        self.assertFalse(contract["aggregate_supply_equals_exchange_flow"])
        self.assertFalse(contract["trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
