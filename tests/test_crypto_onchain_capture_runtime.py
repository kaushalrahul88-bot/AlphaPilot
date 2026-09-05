import unittest

from app.crypto_onchain_capture_runtime import (
    CryptoOnchainCaptureRuntimeConfig,
    ENV_ARCHIVE_ENABLED,
    ENV_DATABASE_URL,
    ENV_GLASSNODE_API_KEY,
    ENV_GLASSNODE_ENABLED,
    ENV_GLASSNODE_INTERVAL,
    ENV_GLASSNODE_LOOKBACK_HOURS,
    ENV_GLASSNODE_METRICS,
    ENV_GLASSNODE_POLL_SECONDS,
    architecture_contract,
    build_crypto_onchain_capture_runtime,
    runtime_status,
)


class CryptoOnchainCaptureRuntimeTests(unittest.TestCase):
    def test_default_environment_disables_glassnode(self):
        config = CryptoOnchainCaptureRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.glassnode_enabled)
        runtime = build_crypto_onchain_capture_runtime(config)
        self.assertEqual(runtime["status"], "CRYPTO_ONCHAIN_RUNTIME_DISABLED")
        self.assertIsNone(runtime["provider"])
        self.assertIsNone(runtime["scheduler"])

    def test_glassnode_cannot_run_without_archive(self):
        with self.assertRaises(ValueError):
            CryptoOnchainCaptureRuntimeConfig.from_env({
                ENV_GLASSNODE_ENABLED: "true",
                ENV_GLASSNODE_API_KEY: "secret",
                ENV_DATABASE_URL: "postgresql://example/test",
            })

    def test_glassnode_enabled_requires_api_key(self):
        with self.assertRaises(ValueError):
            CryptoOnchainCaptureRuntimeConfig.from_env({
                ENV_ARCHIVE_ENABLED: "true",
                ENV_GLASSNODE_ENABLED: "true",
                ENV_DATABASE_URL: "postgresql://example/test",
            })

    def test_archive_only_mode_does_not_enable_provider(self):
        config = CryptoOnchainCaptureRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
        })
        runtime = build_crypto_onchain_capture_runtime(config)
        self.assertEqual(runtime["status"], "CRYPTO_ONCHAIN_ARCHIVE_ONLY_READY")
        self.assertFalse(runtime["provider"].policy.enabled)
        self.assertFalse(runtime["scheduler"].policy.enabled)

    def test_explicit_glassnode_config_is_separate_and_configurable(self):
        config = CryptoOnchainCaptureRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
            ENV_GLASSNODE_ENABLED: "true",
            ENV_GLASSNODE_API_KEY: "secret",
            ENV_GLASSNODE_POLL_SECONDS: "900",
            ENV_GLASSNODE_INTERVAL: "1h",
            ENV_GLASSNODE_LOOKBACK_HOURS: "8",
            ENV_GLASSNODE_METRICS: "MVRV,SOPR,EXCHANGE_NETFLOW",
        })
        runtime = build_crypto_onchain_capture_runtime(config)
        self.assertEqual(runtime["status"], "CRYPTO_ONCHAIN_RUNTIME_READY")
        self.assertTrue(runtime["provider"].policy.enabled)
        self.assertTrue(runtime["scheduler"].policy.enabled)
        self.assertEqual(runtime["scheduler"].policy.poll_seconds, 900)
        self.assertEqual(runtime["provider"].policy.metrics, ("MVRV", "SOPR", "EXCHANGE_NETFLOW"))
        self.assertEqual(runtime["provider"].policy.lookback_hours, 8)

    def test_status_is_descriptive_without_network(self):
        status = runtime_status(CryptoOnchainCaptureRuntimeConfig())
        self.assertFalse(status["glassnode_enabled"])
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["automatic_startup_registration"])
        self.assertFalse(status["trade_generation_enabled"])

    def test_invalid_values_fail_closed(self):
        with self.assertRaises(ValueError):
            CryptoOnchainCaptureRuntimeConfig.from_env({ENV_GLASSNODE_ENABLED: "maybe"})
        with self.assertRaises(ValueError):
            CryptoOnchainCaptureRuntimeConfig.from_env({ENV_GLASSNODE_POLL_SECONDS: "59"})
        with self.assertRaises(ValueError):
            CryptoOnchainCaptureRuntimeConfig.from_env({ENV_GLASSNODE_METRICS: "MVRV,UNKNOWN"})
        with self.assertRaises(ValueError):
            CryptoOnchainCaptureRuntimeConfig.from_env({ENV_GLASSNODE_LOOKBACK_HOURS: "0"})

    def test_contract_prevents_other_capture_switches_from_enabling_onchain(self):
        contract = architecture_contract()
        self.assertFalse(contract["glassnode_enabled_by_default"])
        self.assertTrue(contract["api_key_required_when_enabled"])
        self.assertFalse(contract["derivatives_or_news_switch_enables_onchain"])
        self.assertFalse(contract["automatic_startup_registration"])
        self.assertFalse(contract["automatic_network_request"])
        self.assertFalse(contract["trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
