import unittest
from unittest.mock import Mock, patch

from app.crypto_btc_context_capture_startup import COLLECTORS, architecture_contract


class BtcContextCaptureStartupTests(unittest.TestCase):
    def test_only_verified_keyless_context_collectors_are_registered(self):
        self.assertEqual(
            [collector.name for collector in COLLECTORS],
            ["deribit-options-context", "defillama-stablecoin-supply"],
        )

    def test_each_collector_retains_its_independent_enable_gate(self):
        for collector in COLLECTORS:
            disabled = Mock(archive_enabled=True, deribit_enabled=False, stablecoin_enabled=False)
            self.assertFalse(collector.enabled(disabled))

    def test_contract_preserves_trade_separation(self):
        contract = architecture_contract()
        self.assertTrue(contract["independent_environment_gates"])
        self.assertFalse(contract["options_context_may_select_delta_contract"])
        self.assertFalse(contract["futures_trade_generation_enabled"])
        self.assertFalse(contract["live_execution"])


if __name__ == "__main__":
    unittest.main()
