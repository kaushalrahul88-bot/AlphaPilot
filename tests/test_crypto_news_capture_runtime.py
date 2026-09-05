import unittest

from app.crypto_news_capture_runtime import (
    CryptoNewsCaptureRuntimeConfig,
    ENV_ARCHIVE_ENABLED,
    ENV_DATABASE_URL,
    ENV_NEWS_API_KEY,
    ENV_NEWS_ENABLED,
    ENV_NEWS_LANGUAGE,
    ENV_NEWS_LOOKBACK_MINUTES,
    ENV_NEWS_POLL_SECONDS,
    ENV_NEWS_QUERY,
    architecture_contract,
    build_crypto_news_capture_runtime,
    runtime_status,
)


class CryptoNewsCaptureRuntimeTests(unittest.TestCase):
    def test_default_environment_disables_news_and_archive(self):
        config = CryptoNewsCaptureRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.news_enabled)
        runtime = build_crypto_news_capture_runtime(config)
        self.assertEqual(runtime["status"], "CRYPTO_NEWS_RUNTIME_DISABLED")
        self.assertIsNone(runtime["provider"])
        self.assertIsNone(runtime["scheduler"])

    def test_news_cannot_run_without_archive(self):
        with self.assertRaises(ValueError):
            CryptoNewsCaptureRuntimeConfig.from_env({
                ENV_NEWS_ENABLED: "true",
                ENV_NEWS_API_KEY: "secret",
                ENV_DATABASE_URL: "postgresql://example/test",
            })

    def test_news_enabled_requires_api_key(self):
        with self.assertRaises(ValueError):
            CryptoNewsCaptureRuntimeConfig.from_env({
                ENV_ARCHIVE_ENABLED: "true",
                ENV_NEWS_ENABLED: "true",
                ENV_DATABASE_URL: "postgresql://example/test",
            })

    def test_archive_only_mode_does_not_enable_provider_network(self):
        config = CryptoNewsCaptureRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
        })
        runtime = build_crypto_news_capture_runtime(config)
        self.assertEqual(runtime["status"], "CRYPTO_NEWS_ARCHIVE_ONLY_READY")
        self.assertFalse(runtime["provider"].policy.enabled)
        self.assertFalse(runtime["scheduler"].policy.enabled)

    def test_explicit_news_config_is_separate_and_configurable(self):
        config = CryptoNewsCaptureRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_NEWS_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
            ENV_NEWS_API_KEY: "secret",
            ENV_NEWS_POLL_SECONDS: "90",
            ENV_NEWS_QUERY: "bitcoin OR ethereum",
            ENV_NEWS_LANGUAGE: "en",
            ENV_NEWS_LOOKBACK_MINUTES: "20",
        })
        runtime = build_crypto_news_capture_runtime(config)
        self.assertEqual(runtime["status"], "CRYPTO_NEWS_RUNTIME_READY")
        self.assertTrue(runtime["provider"].policy.enabled)
        self.assertTrue(runtime["scheduler"].policy.enabled)
        self.assertEqual(runtime["scheduler"].policy.poll_seconds, 90)
        self.assertEqual(runtime["provider"].policy.query, "bitcoin OR ethereum")
        self.assertEqual(runtime["provider"].policy.lookback_minutes, 20)

    def test_status_does_not_perform_network_request(self):
        config = CryptoNewsCaptureRuntimeConfig()
        status = runtime_status(config)
        self.assertFalse(status["news_enabled"])
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["automatic_startup_registration"])
        self.assertFalse(status["trade_generation_enabled"])

    def test_invalid_runtime_values_fail_closed(self):
        with self.assertRaises(ValueError):
            CryptoNewsCaptureRuntimeConfig.from_env({ENV_NEWS_ENABLED: "maybe"})
        with self.assertRaises(ValueError):
            CryptoNewsCaptureRuntimeConfig.from_env({ENV_NEWS_POLL_SECONDS: "29"})
        with self.assertRaises(ValueError):
            CryptoNewsCaptureRuntimeConfig.from_env({ENV_NEWS_LOOKBACK_MINUTES: "0"})

    def test_contract_prevents_derivatives_switch_from_enabling_news(self):
        contract = architecture_contract()
        self.assertFalse(contract["news_enabled_by_default"])
        self.assertTrue(contract["api_key_required_when_enabled"])
        self.assertFalse(contract["btc_derivatives_capture_enables_news"])
        self.assertFalse(contract["automatic_startup_registration"])
        self.assertFalse(contract["automatic_network_request"])
        self.assertFalse(contract["feed_assigns_truth_or_direction"])
        self.assertFalse(contract["trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
