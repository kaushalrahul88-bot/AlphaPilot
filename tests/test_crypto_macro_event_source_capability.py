import unittest

from app.crypto_btc_source_capabilities import architecture_contract, capability_for, live_capture_plan


class CryptoMacroEventSourceCapabilityTests(unittest.TestCase):
    def test_release_and_consensus_are_distinct_pit_authorities(self):
        consensus = capability_for("MACRO_CONSENSUS_SNAPSHOTS")
        release = capability_for("SCHEDULED_MACRO_RELEASES")
        self.assertEqual(consensus.provider, "TRADING_ECONOMICS")
        self.assertEqual(consensus.historical_mode, "FIRST_SEEN_ARCHIVE_REQUIRED")
        self.assertFalse(consensus.can_reconstruct_later)
        self.assertEqual(consensus.live_capture_priority, "MEDIUM")
        self.assertEqual(consensus.decision_role, "CONTEXT_ONLY")
        self.assertIn("strictly before the official release", consensus.point_in_time_requirement)
        self.assertIn("DateSpan=0", consensus.point_in_time_requirement)
        self.assertIn("api.tradingeconomics.com/calendar/country", consensus.documented_endpoint)
        self.assertIn("TEForecast", consensus.notes)
        self.assertEqual(release.historical_mode, "OFFICIAL_RELEASE_ARCHIVE")
        self.assertEqual(release.decision_role, "DIRECTIONAL_EVIDENCE")
        self.assertNotEqual(consensus.dataset, release.dataset)

    def test_capture_plan_prioritizes_both_consensus_and_official_release(self):
        plan = live_capture_plan()
        self.assertEqual(plan["version"], "BTC_LIVE_CAPTURE_PLAN_V10")
        self.assertIn("MACRO_CONSENSUS_SNAPSHOTS", plan["capture_medium_priority"])
        self.assertIn("SCHEDULED_MACRO_RELEASES", plan["capture_medium_priority"])
        self.assertTrue(plan["macro_consensus_requires_pre_release_first_seen_archive"])
        self.assertTrue(plan["macro_consensus_is_not_directional_evidence"])
        self.assertTrue(plan["tradingeconomics_consensus_provider_verified"])
        self.assertTrue(plan["tradingeconomics_teforecast_is_not_consensus"])
        self.assertFalse(plan["tradingeconomics_historical_pit_backfill_enabled"])

    def test_architecture_forbids_hindsight_consensus_and_revision_overwrite(self):
        contract = architecture_contract()
        self.assertEqual(contract["version"], "BTC_SOURCE_CAPABILITY_CONTRACT_V10")
        self.assertFalse(contract["macro_consensus_learned_after_release_may_be_backdated"])
        self.assertFalse(contract["macro_consensus_may_assign_btc_direction"])
        self.assertFalse(contract["tradingeconomics_teforecast_may_substitute_consensus"])
        self.assertFalse(contract["tradingeconomics_historical_pit_backfill_enabled"])
        self.assertFalse(contract["scheduled_macro_revision_may_replace_first_release"])
        self.assertFalse(contract["futures_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
