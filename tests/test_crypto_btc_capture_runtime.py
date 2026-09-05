import unittest

from app.crypto_btc_capture_runtime import (
    BtcCaptureRuntimeConfig,
    ENV_ARCHIVE_ENABLED,
    ENV_CAPTURE_ENABLED,
    ENV_COINGLASS_API_KEY,
    ENV_COINGLASS_ENABLED,
    ENV_COINGLASS_INTERVAL,
    ENV_COINGLASS_POLL_SECONDS,
    ENV_DATABASE_URL,
    ENV_POLL_SECONDS,
    architecture_contract,
    build_btc_capture_runtime,
    runtime_status,
)
from app.crypto_btc_capture_scheduler import (
    COINDCX_FUTURES_RT_JOB,
    COINGLASS_LIQUIDATIONS_JOB,
    COINGLASS_OPEN_INTEREST_JOB,
)


class CryptoBtcCaptureRuntimeTests(unittest.TestCase):
    def test_default_environment_keeps_archive_capture_and_coinglass_disabled(self):
        config = BtcCaptureRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.capture_enabled)
        self.assertFalse(config.coinglass_enabled)
        runtime = build_btc_capture_runtime(config)
        self.assertEqual(runtime["status"], "BTC_PIT_RUNTIME_DISABLED")
        self.assertIsNone(runtime["store"])
        self.assertIsNone(runtime["provider"])
        self.assertIsNone(runtime["coinglass_provider"])
        self.assertIsNone(runtime["scheduler"])

    def test_capture_cannot_be_enabled_without_archive(self):
        with self.assertRaises(ValueError):
            BtcCaptureRuntimeConfig.from_env({
                ENV_CAPTURE_ENABLED: "true",
                ENV_DATABASE_URL: "postgresql://example/test",
            })

    def test_archive_cannot_be_enabled_without_database(self):
        with self.assertRaises(ValueError):
            BtcCaptureRuntimeConfig.from_env({ENV_ARCHIVE_ENABLED: "true"})

    def test_archive_only_runtime_has_disabled_provider_and_scheduler(self):
        config = BtcCaptureRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
        })
        runtime = build_btc_capture_runtime(config)
        self.assertEqual(runtime["status"], "BTC_PIT_ARCHIVE_ONLY_READY")
        self.assertFalse(runtime["provider"].policy.enabled)
        self.assertIsNone(runtime["coinglass_provider"])
        self.assertFalse(runtime["scheduler"].policy.enabled)

    def test_explicit_base_capture_does_not_enable_coinglass(self):
        config = BtcCaptureRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "1",
            ENV_CAPTURE_ENABLED: "yes",
            ENV_DATABASE_URL: "postgresql://example/test",
            ENV_POLL_SECONDS: "120",
        })
        runtime = build_btc_capture_runtime(config)
        self.assertEqual(runtime["status"], "BTC_PIT_RUNTIME_READY")
        self.assertTrue(runtime["provider"].policy.enabled)
        self.assertIsNone(runtime["coinglass_provider"])
        self.assertTrue(runtime["scheduler"].policy.enabled)
        self.assertEqual(runtime["scheduler"].policy.enabled_jobs, (COINDCX_FUTURES_RT_JOB,))

    def test_coinglass_requires_separate_opt_in_and_api_key(self):
        base = {
            ENV_ARCHIVE_ENABLED: "true",
            ENV_CAPTURE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
            ENV_COINGLASS_ENABLED: "true",
        }
        with self.assertRaises(ValueError):
            BtcCaptureRuntimeConfig.from_env(base)
        with self.assertRaises(ValueError):
            BtcCaptureRuntimeConfig.from_env({
                ENV_ARCHIVE_ENABLED: "true",
                ENV_DATABASE_URL: "postgresql://example/test",
                ENV_COINGLASS_ENABLED: "true",
                ENV_COINGLASS_API_KEY: "secret",
            })

    def test_explicit_coinglass_capture_enables_only_research_jobs(self):
        config = BtcCaptureRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_CAPTURE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
            ENV_COINGLASS_ENABLED: "true",
            ENV_COINGLASS_API_KEY: "secret",
            ENV_COINGLASS_INTERVAL: "4h",
            ENV_COINGLASS_POLL_SECONDS: "600",
        })
        runtime = build_btc_capture_runtime(config)
        self.assertIsNotNone(runtime["coinglass_provider"])
        self.assertTrue(runtime["coinglass_provider"].policy.enabled)
        self.assertEqual(
            set(runtime["scheduler"].policy.enabled_jobs),
            {COINDCX_FUTURES_RT_JOB, COINGLASS_OPEN_INTEREST_JOB, COINGLASS_LIQUIDATIONS_JOB},
        )
        self.assertEqual(runtime["scheduler"].policy.coinglass_poll_seconds, 600)

    def test_runtime_status_is_descriptive_and_does_not_start_network(self):
        config = BtcCaptureRuntimeConfig()
        status = runtime_status(config)
        self.assertFalse(status["archive_enabled"])
        self.assertFalse(status["capture_enabled"])
        self.assertFalse(status["coinglass_enabled"])
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["automatic_startup_registration"])

    def test_invalid_boolean_and_poll_interval_fail_closed(self):
        with self.assertRaises(ValueError):
            BtcCaptureRuntimeConfig.from_env({ENV_ARCHIVE_ENABLED: "maybe"})
        with self.assertRaises(ValueError):
            BtcCaptureRuntimeConfig.from_env({ENV_POLL_SECONDS: "9"})
        with self.assertRaises(ValueError):
            BtcCaptureRuntimeConfig.from_env({ENV_COINGLASS_POLL_SECONDS: "59"})

    def test_architecture_contract_has_no_auto_activation(self):
        contract = architecture_contract()
        self.assertFalse(contract["archive_enabled_by_default"])
        self.assertFalse(contract["capture_enabled_by_default"])
        self.assertFalse(contract["coinglass_enabled_by_default"])
        self.assertTrue(contract["coinglass_has_separate_opt_in"])
        self.assertFalse(contract["automatic_startup_registration"])
        self.assertFalse(contract["automatic_network_request"])
        self.assertFalse(contract["capture_without_archive_allowed"])
        self.assertFalse(contract["options_execution_enabled"])
        self.assertFalse(contract["futures_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
