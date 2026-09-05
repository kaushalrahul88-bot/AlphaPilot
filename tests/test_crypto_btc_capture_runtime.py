import unittest

from app.crypto_btc_capture_runtime import (
    BtcCaptureRuntimeConfig,
    ENV_ARCHIVE_ENABLED,
    ENV_CAPTURE_ENABLED,
    ENV_DATABASE_URL,
    ENV_POLL_SECONDS,
    architecture_contract,
    build_btc_capture_runtime,
    runtime_status,
)


class CryptoBtcCaptureRuntimeTests(unittest.TestCase):
    def test_default_environment_keeps_archive_and_capture_disabled(self):
        config = BtcCaptureRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.capture_enabled)
        runtime = build_btc_capture_runtime(config)
        self.assertEqual(runtime["status"], "BTC_PIT_RUNTIME_DISABLED")
        self.assertIsNone(runtime["store"])
        self.assertIsNone(runtime["provider"])
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
        self.assertFalse(runtime["scheduler"].policy.enabled)

    def test_explicit_capture_runtime_enables_provider_and_scheduler_only(self):
        config = BtcCaptureRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "1",
            ENV_CAPTURE_ENABLED: "yes",
            ENV_DATABASE_URL: "postgresql://example/test",
            ENV_POLL_SECONDS: "120",
        })
        runtime = build_btc_capture_runtime(config)
        self.assertEqual(runtime["status"], "BTC_PIT_RUNTIME_READY")
        self.assertTrue(runtime["provider"].policy.enabled)
        self.assertTrue(runtime["scheduler"].policy.enabled)
        self.assertEqual(runtime["scheduler"].policy.poll_seconds, 120)

    def test_runtime_status_is_descriptive_and_does_not_start_network(self):
        config = BtcCaptureRuntimeConfig()
        status = runtime_status(config)
        self.assertFalse(status["archive_enabled"])
        self.assertFalse(status["capture_enabled"])
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["automatic_startup_registration"])

    def test_invalid_boolean_and_poll_interval_fail_closed(self):
        with self.assertRaises(ValueError):
            BtcCaptureRuntimeConfig.from_env({ENV_ARCHIVE_ENABLED: "maybe"})
        with self.assertRaises(ValueError):
            BtcCaptureRuntimeConfig.from_env({ENV_POLL_SECONDS: "9"})

    def test_architecture_contract_has_no_auto_activation(self):
        contract = architecture_contract()
        self.assertFalse(contract["archive_enabled_by_default"])
        self.assertFalse(contract["capture_enabled_by_default"])
        self.assertFalse(contract["automatic_startup_registration"])
        self.assertFalse(contract["automatic_network_request"])
        self.assertFalse(contract["capture_without_archive_allowed"])
        self.assertFalse(contract["options_execution_enabled"])
        self.assertFalse(contract["futures_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
