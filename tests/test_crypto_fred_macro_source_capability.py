import unittest

from app.crypto_btc_source_capabilities import architecture_contract, capability_for, live_capture_plan


class CryptoFredMacroSourceCapabilityTests(unittest.TestCase):
    def test_historical_vintage_and_live_snapshot_are_distinct_source_authorities(self):
        historical = capability_for("FRED_MACRO_REGIME_VINTAGE_HISTORY")
        live = capability_for("FRED_MACRO_REGIME_LIVE_SNAPSHOT")
        releases = capability_for("SCHEDULED_MACRO_RELEASES")

        self.assertEqual(historical.provider, "FRED_ALFRED")
        self.assertEqual(historical.historical_mode, "RECONSTRUCTIBLE_PUBLIC_HISTORY")
        self.assertTrue(historical.can_reconstruct_later)
        self.assertEqual(historical.live_capture_priority, "LOW")
        self.assertEqual(historical.decision_role, "CONTEXT_ONLY")

        self.assertEqual(live.provider, "FRED_ALFRED")
        self.assertEqual(live.historical_mode, "FIRST_SEEN_ARCHIVE_REQUIRED")
        self.assertFalse(live.can_reconstruct_later)
        self.assertEqual(live.live_capture_priority, "MEDIUM")
        self.assertEqual(live.decision_role, "CONTEXT_ONLY")

        self.assertEqual(releases.historical_mode, "OFFICIAL_RELEASE_ARCHIVE")
        self.assertEqual(releases.decision_role, "DIRECTIONAL_EVIDENCE")
        self.assertNotEqual(historical.dataset, live.dataset)
        self.assertNotEqual(live.dataset, releases.dataset)

    def test_capture_plan_reconstructs_vintage_history_but_archives_live_snapshot(self):
        plan = live_capture_plan()
        self.assertIn("FRED_MACRO_REGIME_VINTAGE_HISTORY", plan["do_not_duplicate_by_default"])
        self.assertIn("FRED_MACRO_REGIME_LIVE_SNAPSHOT", plan["capture_medium_priority"])
        self.assertIn("SCHEDULED_MACRO_RELEASES", plan["capture_medium_priority"])
        self.assertTrue(plan["fred_vintage_history_is_reconstructible"])
        self.assertTrue(plan["fred_same_day_live_snapshot_requires_first_seen_archive"])
        self.assertTrue(plan["fred_daily_regime_is_not_exact_macro_event_surprise"])

    def test_architecture_forbids_backdating_or_daily_macro_direction_vote(self):
        contract = architecture_contract()
        self.assertFalse(contract["fred_historical_same_day_vintage_proves_intraday_visibility"])
        self.assertFalse(contract["fred_live_first_seen_may_be_backdated"])
        self.assertFalse(contract["fred_daily_regime_may_supply_second_intraday_origin"])
        self.assertFalse(contract["scheduled_macro_revision_may_replace_first_release"])
        self.assertFalse(contract["futures_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
