import unittest

from app.crypto_btc_source_capabilities import architecture_contract, capability_for, live_capture_plan


class CryptoDeribitOptionsSourceCapabilityTests(unittest.TestCase):
    def test_global_options_context_is_distinct_from_exact_coindcx_chain(self):
        global_context = capability_for("BTC_GLOBAL_OPTIONS_CONTEXT")
        coindcx_chain = capability_for("COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES")
        self.assertEqual(global_context.provider, "DERIBIT")
        self.assertEqual(global_context.historical_mode, "FIRST_SEEN_ARCHIVE_REQUIRED")
        self.assertEqual(global_context.decision_role, "OPTIONS_TRANSLATION")
        self.assertEqual(global_context.live_capture_priority, "HIGH")
        self.assertFalse(global_context.can_reconstruct_later)
        self.assertEqual(coindcx_chain.provider, "COINDCX")
        self.assertEqual(coindcx_chain.historical_mode, "UNCONFIRMED")
        self.assertEqual(coindcx_chain.live_capture_priority, "CRITICAL")
        self.assertNotEqual(global_context.dataset, coindcx_chain.dataset)

    def test_capture_plan_prioritizes_context_but_keeps_exact_coindcx_archive_gate(self):
        plan = live_capture_plan()
        self.assertIn("BTC_GLOBAL_OPTIONS_CONTEXT", plan["capture_high_priority"])
        self.assertIn("COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES", plan["capture_first"])
        self.assertTrue(plan["options_archive_required_before_economic_backtest"])
        self.assertTrue(plan["global_options_context_is_not_coindcx_execution_data"])

    def test_architecture_forbids_global_context_from_coindcx_fill_or_selection(self):
        contract = architecture_contract()
        self.assertFalse(contract["global_options_context_may_select_coindcx_contract"])
        self.assertFalse(contract["global_options_context_may_fill_coindcx_replay"])
        self.assertFalse(contract["option_history_may_be_fabricated"])
        self.assertFalse(contract["futures_execution_enabled"])

    def test_resolved_experience_capability_uses_dedicated_memory_semantics(self):
        memory = capability_for("ALPHAPILOT_PRIOR_RESOLVED_EXPERIENCE")
        self.assertIn("dedicated immutable Experience Memory store", memory.point_in_time_requirement)
        self.assertIn("not the market-data PIT archive", memory.notes)
        self.assertEqual(memory.decision_role, "CONTEXT_ONLY")


if __name__ == "__main__":
    unittest.main()
