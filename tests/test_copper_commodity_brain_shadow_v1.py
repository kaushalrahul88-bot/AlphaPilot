from __future__ import annotations

import unittest
from unittest.mock import patch

from app.copper_commodity_brain_shadow_v1 import (
    evaluate_copper_commodity_brain_shadow,
    integration_contract,
)


def _row(family: str, origin: str, stance: str = "UNKNOWN", counts: bool = False) -> dict:
    return {
        "family": family,
        "causal_origin": origin,
        "stance": stance,
        "counts_for_direction": counts,
        "state": "TEST",
        "detail": {},
    }


class CopperCommodityBrainSharedShadowTests(unittest.TestCase):
    @patch("app.copper_commodity_brain_shadow_v1.experience_memory_family")
    @patch("app.copper_commodity_brain_shadow_v1.event_reaction_family")
    @patch("app.copper_commodity_brain_shadow_v1.china_demand_family")
    @patch("app.copper_commodity_brain_shadow_v1.global_copper_family")
    @patch("app.copper_commodity_brain_shadow_v1.option_participation_family")
    @patch("app.copper_commodity_brain_shadow_v1.local_structure_family")
    def test_local_plus_option_can_form_moderate_direction(
        self, local, option, global_copper, china, event, memory
    ):
        local.return_value = _row("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH", True)
        option.return_value = _row("OPTION_PARTICIPATION", "OPTION_MARKET_POSITIONING", "BULLISH", True)
        global_copper.return_value = _row("GLOBAL_COPPER", "GLOBAL_COPPER_PRICE_DISCOVERY")
        china.return_value = _row("CHINA_DEMAND", "CHINA_PHYSICAL_AND_MACRO_DEMAND")
        event.return_value = _row("EVENT_REACTION", "COPPER_EVENT_CAUSAL_REACTION")
        memory.return_value = _row("EXPERIENCE_MEMORY", "HISTORICAL_ANALOGUE")

        result = evaluate_copper_commodity_brain_shadow({"as_of": "2026-09-04T12:00:00+05:30"})
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["direction_confidence"], "MODERATE")
        self.assertEqual(
            set(result["counted_families"]),
            {"LOCAL_STRUCTURE", "OPTION_PARTICIPATION"},
        )
        self.assertEqual(result["decision_effect"], "NONE")
        self.assertFalse(result["live_execution_enabled"])
        self.assertEqual(result["capital_committed"], 0)

    @patch("app.copper_commodity_brain_shadow_v1.experience_memory_family")
    @patch("app.copper_commodity_brain_shadow_v1.event_reaction_family")
    @patch("app.copper_commodity_brain_shadow_v1.china_demand_family")
    @patch("app.copper_commodity_brain_shadow_v1.global_copper_family")
    @patch("app.copper_commodity_brain_shadow_v1.option_participation_family")
    @patch("app.copper_commodity_brain_shadow_v1.local_structure_family")
    def test_registered_legacy_memory_cannot_be_second_confirmation(
        self, local, option, global_copper, china, event, memory
    ):
        local.return_value = _row("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH", True)
        option.return_value = _row("OPTION_PARTICIPATION", "OPTION_MARKET_POSITIONING")
        global_copper.return_value = _row("GLOBAL_COPPER", "GLOBAL_COPPER_PRICE_DISCOVERY")
        china.return_value = _row("CHINA_DEMAND", "CHINA_PHYSICAL_AND_MACRO_DEMAND")
        event.return_value = _row("EVENT_REACTION", "COPPER_EVENT_CAUSAL_REACTION")
        memory.return_value = _row("EXPERIENCE_MEMORY", "HISTORICAL_ANALOGUE", "BULLISH", True)

        result = evaluate_copper_commodity_brain_shadow({"as_of": "2026-09-04T12:00:00+05:30"})
        self.assertEqual(result["direction"], "UNKNOWN")
        memory_result = result["families"]["EXPERIENCE_MEMORY"]
        self.assertFalse(memory_result["counts_for_direction"])
        self.assertEqual(memory_result["role"], "MEMORY")
        self.assertEqual(memory_result["depends_on_origins"], ["LOCAL_PRICE_STRUCTURE"])
        self.assertFalse(result["integration_contract"]["experience_memory_can_satisfy_confirmation_gate"])

    @patch("app.copper_commodity_brain_shadow_v1.experience_memory_family")
    @patch("app.copper_commodity_brain_shadow_v1.event_reaction_family")
    @patch("app.copper_commodity_brain_shadow_v1.china_demand_family")
    @patch("app.copper_commodity_brain_shadow_v1.global_copper_family")
    @patch("app.copper_commodity_brain_shadow_v1.option_participation_family")
    @patch("app.copper_commodity_brain_shadow_v1.local_structure_family")
    def test_opposing_local_and_option_force_conflict(
        self, local, option, global_copper, china, event, memory
    ):
        local.return_value = _row("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH", True)
        option.return_value = _row("OPTION_PARTICIPATION", "OPTION_MARKET_POSITIONING", "BEARISH", True)
        global_copper.return_value = _row("GLOBAL_COPPER", "GLOBAL_COPPER_PRICE_DISCOVERY")
        china.return_value = _row("CHINA_DEMAND", "CHINA_PHYSICAL_AND_MACRO_DEMAND")
        event.return_value = _row("EVENT_REACTION", "COPPER_EVENT_CAUSAL_REACTION")
        memory.return_value = _row("EXPERIENCE_MEMORY", "HISTORICAL_ANALOGUE")

        result = evaluate_copper_commodity_brain_shadow({"as_of": "2026-09-04T12:00:00+05:30"})
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["direction_confidence"], "CONFLICTED")

    def test_contract_does_not_replace_or_rewrite_existing_copper(self):
        contract = integration_contract()
        self.assertTrue(contract["research_only"])
        self.assertTrue(contract["shadow_only"])
        self.assertFalse(contract["old_copper_v1_v2_replaced"])
        self.assertFalse(contract["stored_copper_predictions_rewritten"])
        self.assertEqual(contract["current_mind_effect"], "NONE")
        self.assertFalse(contract["live_execution_enabled"])
        self.assertFalse(contract["broker_order_placement_enabled"])
        self.assertFalse(contract["promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
