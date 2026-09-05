import unittest

from app.crypto_btc_source_capabilities import (
    architecture_contract,
    capability_for,
    live_capture_plan,
    source_capability_registry,
)


class BtcSourceCapabilityTests(unittest.TestCase):
    def test_registry_has_unique_datasets_and_required_lanes(self):
        rows = source_capability_registry()
        keys = {(row["lane"], row["dataset"], row["provider"]) for row in rows}
        self.assertEqual(len(keys), len(rows))
        lanes = {row["lane"] for row in rows}
        self.assertTrue({
            "SPOT_STRUCTURE",
            "DERIVATIVES_POSITIONING",
            "OPTIONS_MARKET",
            "NEWS",
            "SOCIAL_NARRATIVE",
            "ONCHAIN",
            "STABLECOIN_LIQUIDITY",
            "MACRO_CROSS_ASSET",
            "HISTORICAL_MEMORY",
        }.issubset(lanes))

    def test_spot_and_futures_ohlcv_are_reconstructible_public_history(self):
        spot = capability_for("BTC_SPOT_OHLCV")
        futures = capability_for("BTC_FUTURES_OHLCV")
        self.assertEqual(spot.historical_mode, "RECONSTRUCTIBLE_PUBLIC_HISTORY")
        self.assertEqual(futures.historical_mode, "RECONSTRUCTIBLE_PUBLIC_HISTORY")
        self.assertTrue(spot.can_reconstruct_later)
        self.assertTrue(futures.can_reconstruct_later)
        self.assertEqual(spot.live_capture_priority, "LOW")
        self.assertEqual(futures.live_capture_priority, "LOW")

    def test_current_futures_funding_snapshot_is_not_mislabeled_historical(self):
        row = capability_for("BTC_FUTURES_FUNDING_MARK_SNAPSHOT")
        self.assertEqual(row.historical_mode, "FIRST_SEEN_ARCHIVE_REQUIRED")
        self.assertFalse(row.can_reconstruct_later)
        self.assertEqual(row.live_capture_priority, "CRITICAL")

    def test_open_interest_is_never_inferred_from_volume(self):
        row = capability_for("BTC_OPEN_INTEREST")
        self.assertFalse(row.can_reconstruct_later)
        self.assertIn("Do not infer OI", row.notes)

    def test_options_history_remains_unconfirmed_and_must_not_be_fabricated(self):
        row = capability_for("COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES")
        self.assertEqual(row.historical_mode, "UNCONFIRMED")
        self.assertFalse(row.can_reconstruct_later)
        self.assertEqual(row.decision_role, "OPTIONS_TRANSLATION")
        self.assertEqual(row.live_capture_priority, "CRITICAL")

    def test_actual_option_exit_quotes_are_capture_first(self):
        row = capability_for("BTC_OPTION_EXIT_QUOTES")
        plan = live_capture_plan()
        self.assertIn(row.dataset, plan["capture_first"])
        self.assertTrue(plan["options_archive_required_before_economic_backtest"])

    def test_reconstructible_candles_are_not_default_storage_priority(self):
        plan = live_capture_plan()
        self.assertIn("BTC_SPOT_OHLCV", plan["do_not_duplicate_by_default"])
        self.assertIn("BTC_FUTURES_OHLCV", plan["do_not_duplicate_by_default"])
        self.assertNotIn("BTC_FUTURES_FUNDING_MARK_SNAPSHOT", plan["do_not_duplicate_by_default"])

    def test_news_social_onchain_require_point_in_time_availability(self):
        for dataset in (
            "CRYPTO_NEWS_EVENTS",
            "CRYPTO_SOCIAL_POSTS_AND_NARRATIVE_VELOCITY",
            "CRYPTO_SOCIAL_ENRICHMENT",
            "BTC_ONCHAIN_ENTITY_AND_FLOW_METRICS",
        ):
            row = capability_for(dataset)
            self.assertFalse(row.can_reconstruct_later)
            self.assertIn(row.historical_mode, {"EXTERNAL_PIT_ARCHIVE_REQUIRED", "FIRST_SEEN_ARCHIVE_REQUIRED"})

    def test_social_raw_requires_approved_source_and_enrichment_is_separate_context(self):
        raw = capability_for("CRYPTO_SOCIAL_POSTS_AND_NARRATIVE_VELOCITY")
        enrichment = capability_for("CRYPTO_SOCIAL_ENRICHMENT")
        plan = live_capture_plan()
        contract = architecture_contract()
        self.assertEqual(raw.provider, "APPROVED_OR_LICENSED_SOURCE")
        self.assertEqual(raw.decision_role, "CONTEXT_ONLY")
        self.assertEqual(enrichment.provider, "ALPHAPILOT")
        self.assertEqual(enrichment.historical_mode, "FIRST_SEEN_ARCHIVE_REQUIRED")
        self.assertEqual(enrichment.decision_role, "CONTEXT_ONLY")
        self.assertIn(raw.dataset, plan["capture_high_priority"])
        self.assertIn(enrichment.dataset, plan["capture_high_priority"])
        self.assertTrue(plan["social_raw_and_enrichment_have_separate_first_seen_state"])
        self.assertTrue(plan["social_provider_requires_verified_rights"])
        self.assertFalse(contract["social_public_visibility_equals_retention_permission"])
        self.assertFalse(contract["social_analysis_learned_later_may_be_backdated"])
        self.assertFalse(contract["raw_social_may_be_rewritten_by_enrichment"])

    def test_aggregate_stablecoin_supply_is_context_only_and_separate_from_venue_flows(self):
        aggregate = capability_for("STABLECOIN_SUPPLY_LIQUIDITY")
        venue = capability_for("STABLECOIN_EXCHANGE_AND_CHAIN_FLOWS")
        plan = live_capture_plan()
        self.assertEqual(aggregate.provider, "DEFILLAMA")
        self.assertEqual(aggregate.historical_mode, "FIRST_SEEN_ARCHIVE_REQUIRED")
        self.assertEqual(aggregate.decision_role, "CONTEXT_ONLY")
        self.assertFalse(aggregate.can_reconstruct_later)
        self.assertEqual(venue.historical_mode, "EXTERNAL_PIT_ARCHIVE_REQUIRED")
        self.assertEqual(venue.decision_role, "DIRECTIONAL_EVIDENCE")
        self.assertNotEqual(aggregate.dataset, venue.dataset)
        self.assertIn(aggregate.dataset, plan["capture_high_priority"])
        self.assertIn(venue.dataset, plan["capture_high_priority"])

    def test_macro_release_revisions_cannot_replace_first_release(self):
        row = capability_for("SCHEDULED_MACRO_RELEASES")
        self.assertEqual(row.historical_mode, "OFFICIAL_RELEASE_ARCHIVE")
        contract = architecture_contract()
        self.assertFalse(contract["scheduled_macro_revision_may_replace_first_release"])

    def test_massive_cme_cross_asset_reaction_is_replay_only_and_trade_separated(self):
        row = capability_for("CROSS_ASSET_MARKET_PRICES")
        plan = live_capture_plan()
        contract = architecture_contract()
        self.assertEqual(row.provider, "MASSIVE_CME")
        self.assertEqual(row.historical_mode, "RECONSTRUCTIBLE_PUBLIC_HISTORY")
        self.assertTrue(row.can_reconstruct_later)
        self.assertEqual(row.live_capture_priority, "LOW")
        self.assertEqual(row.decision_role, "DIRECTIONAL_EVIDENCE")
        self.assertIn("pre-release", row.point_in_time_requirement)
        self.assertIn("not DXY", row.notes)
        self.assertIn(row.dataset, plan["do_not_duplicate_by_default"])
        self.assertTrue(plan["massive_cme_macro_reaction_replay_verified"])
        self.assertFalse(plan["massive_cme_prospective_live_availability_proven"])
        self.assertTrue(plan["massive_contract_selection_uses_pre_release_data_only"])
        self.assertFalse(plan["massive_post_release_volume_may_select_contract"])
        self.assertTrue(plan["massive_6e_is_inverse_usd_proxy_not_dxy"])
        self.assertFalse(plan["direct_usd_and_proxy_may_double_count"])
        self.assertFalse(plan["massive_macro_reaction_may_generate_futures_trade"])
        self.assertTrue(contract["massive_cme_macro_reaction_replay_verified"])
        self.assertFalse(contract["massive_cme_prospective_live_availability_proven"])
        self.assertTrue(contract["massive_contract_selection_uses_pre_release_data_only"])
        self.assertFalse(contract["massive_post_release_volume_may_select_contract"])
        self.assertFalse(contract["massive_6e_proxy_claimed_to_be_dxy"])
        self.assertFalse(contract["direct_usd_and_proxy_may_double_count"])
        self.assertFalse(contract["massive_macro_reaction_may_generate_futures_trade"])

    def test_futures_context_capture_does_not_enable_futures_execution(self):
        plan = live_capture_plan()
        contract = architecture_contract()
        self.assertTrue(plan["futures_context_capture_does_not_enable_futures_execution"])
        self.assertTrue(contract["futures_context_may_inform_options"])
        self.assertFalse(contract["futures_execution_enabled"])

    def test_architecture_refuses_current_equals_historical_shortcut(self):
        contract = architecture_contract()
        self.assertFalse(contract["undocumented_equals_historical_support"])
        self.assertFalse(contract["current_endpoint_equals_historical_archive"])
        self.assertFalse(contract["future_reconstruction_of_first_seen_state_allowed"])
        self.assertFalse(contract["option_history_may_be_fabricated"])
        self.assertTrue(contract["irrecoverable_pit_state_should_be_storage_priority"])


if __name__ == "__main__":
    unittest.main()
