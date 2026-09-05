import unittest

from app.crypto_deribit_options_capture_runtime import (
    ENV_ARCHIVE_ENABLED,
    ENV_DATABASE_URL,
    ENV_DERIBIT_OPTIONS_ENABLED,
    ENV_DERIBIT_OPTIONS_POLL_SECONDS,
    DeribitOptionsRuntimeConfig,
    architecture_contract,
    build_deribit_options_runtime,
    runtime_status,
)


class CryptoDeribitOptionsCaptureRuntimeTests(unittest.TestCase):
    def test_default_environment_disables_deribit_context(self):
        config = DeribitOptionsRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.deribit_enabled)
        runtime = build_deribit_options_runtime(config)
        self.assertEqual(runtime["status"], "DERIBIT_OPTIONS_CONTEXT_RUNTIME_DISABLED")
        self.assertIsNone(runtime["provider"])
        self.assertIsNone(runtime["scheduler"])

    def test_capture_cannot_run_without_archive(self):
        with self.assertRaises(ValueError):
            DeribitOptionsRuntimeConfig.from_env({
                ENV_DERIBIT_OPTIONS_ENABLED: "true",
                ENV_DATABASE_URL: "postgresql://example/test",
            })

    def test_archive_enabled_requires_database(self):
        with self.assertRaises(ValueError):
            DeribitOptionsRuntimeConfig.from_env({ENV_ARCHIVE_ENABLED: "true"})

    def test_archive_only_mode_keeps_deribit_disabled(self):
        config = DeribitOptionsRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
        })
        runtime = build_deribit_options_runtime(config)
        self.assertEqual(runtime["status"], "DERIBIT_OPTIONS_CONTEXT_ARCHIVE_ONLY_READY")
        self.assertFalse(runtime["provider"].policy.enabled)
        self.assertFalse(runtime["scheduler"].policy.enabled)

    def test_explicit_context_runtime_is_separate_and_configurable(self):
        config = DeribitOptionsRuntimeConfig.from_env({
            ENV_ARCHIVE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
            ENV_DERIBIT_OPTIONS_ENABLED: "true",
            ENV_DERIBIT_OPTIONS_POLL_SECONDS: "600",
        })
        runtime = build_deribit_options_runtime(config)
        self.assertEqual(runtime["status"], "DERIBIT_OPTIONS_CONTEXT_RUNTIME_READY")
        self.assertTrue(runtime["provider"].policy.enabled)
        self.assertTrue(runtime["scheduler"].policy.enabled)
        self.assertEqual(runtime["scheduler"].policy.poll_seconds, 600)

    def test_status_is_descriptive_and_does_not_start_network_or_execution(self):
        status = runtime_status(DeribitOptionsRuntimeConfig())
        self.assertFalse(status["deribit_enabled"])
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["automatic_startup_registration"])
        self.assertFalse(status["instrument_metadata_refresh_automatic"])
        self.assertTrue(status["global_options_context_only"])
        self.assertFalse(status["coindcx_contract_selection_enabled"])
        self.assertFalse(status["coindcx_quote_fill_enabled"])
        self.assertFalse(status["trade_generation_enabled"])

    def test_invalid_values_fail_closed(self):
        with self.assertRaises(ValueError):
            DeribitOptionsRuntimeConfig.from_env({ENV_DERIBIT_OPTIONS_ENABLED: "maybe"})
        with self.assertRaises(ValueError):
            DeribitOptionsRuntimeConfig.from_env({ENV_DERIBIT_OPTIONS_POLL_SECONDS: "59"})

    def test_contract_prevents_cross_activation(self):
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertFalse(contract["api_key_required"])
        self.assertTrue(contract["archive_required_before_capture"])
        self.assertTrue(contract["database_required_before_capture"])
        self.assertFalse(contract["automatic_startup_registration"])
        self.assertFalse(contract["instrument_metadata_refresh_automatic"])
        self.assertFalse(contract["coindcx_options_capture_switch_enables_deribit"])
        self.assertFalse(contract["deribit_switch_enables_coindcx_execution"])
        self.assertTrue(contract["global_options_context_only"])
        self.assertFalse(contract["coindcx_contract_selection_enabled"])
        self.assertFalse(contract["coindcx_quote_fill_enabled"])
        self.assertFalse(contract["trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
