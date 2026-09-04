from __future__ import annotations

import unittest

from app.copper_direction_brain_v2_shadow import (
    _thesis,
    china_demand_family,
    evaluate_copper_direction_v2_shadow,
    integration_contract,
    local_structure_family,
    option_participation_family,
)


def _board(*, structure="UPTREND", ret15=0.2, ret60=0.5, options=True):
    return {
        "as_of": "2026-09-04T23:00:00+05:30",
        "groups": {
            "primary_market": {
                "MCX_COPPER": {
                    "status": "AVAILABLE",
                    "perception_status": "READY",
                    "perception_snapshot": {
                        "structure": structure,
                        "return_15m_pct": ret15,
                        "return_60m_pct": ret60,
                        "session_vwap_gap_pct": 0.1,
                        "opening_range_break": "ABOVE",
                        "price_oi_state": "LONG_BUILDUP",
                    },
                }
            },
            "option_market": {
                "MCX_COPPER_OPTION": {
                    "status": "AVAILABLE" if options else "UNAVAILABLE",
                    "sample_bucket_at": "2026-09-04T22:55:00+05:30",
                    "put_call_oi_ratio": 1.4,
                    "ce_open_interest": 1000,
                    "pe_open_interest": 1400,
                    "first_seen_immutable": True,
                }
            },
            "global_copper": {
                "COMEX_HG": {"status": "UNAVAILABLE", "reason": "NO_TAPE"},
                "LME_COPPER": {"status": "UNAVAILABLE", "reason": "NO_TAPE"},
            },
            "china_macro": {
                "MACRO_RELEASE": {
                    "status": "AVAILABLE",
                    "records": [{"value": {"event": "CHINA_MANUFACTURING_PMI", "actual": 49.2}}],
                }
            },
            "news": {"COPPER_NEWS": {"status": "UNAVAILABLE", "reason": "NO_FIRST_DETECTED_STORE"}},
            "currency": {
                "USDINR_INTRADAY": {"status": "UNAVAILABLE"},
                "SLOW_REFERENCE_FX": {"status": "AVAILABLE", "frequency": "daily"},
            },
            "positioning": {
                "CFTC_COPPER": {"status": "AVAILABLE", "frequency": "weekly"}
            },
        },
    }


class CopperDirectionBrainV2ShadowTests(unittest.TestCase):
    def test_local_structure_is_one_family_even_with_multiple_momentum_inputs(self):
        family = local_structure_family(_board())
        self.assertEqual(family["family"], "LOCAL_STRUCTURE")
        self.assertEqual(family["stance"], "BULLISH")
        self.assertTrue(family["counts_for_direction"])
        self.assertEqual(family["causal_origin"], "LOCAL_PRICE_STRUCTURE")

    def test_internal_local_contradiction_abstains(self):
        family = local_structure_family(_board(structure="UPTREND", ret15=-0.2, ret60=0.5))
        self.assertEqual(family["stance"], "UNKNOWN")
        self.assertFalse(family["counts_for_direction"])
        self.assertEqual(family["state"], "INTERNAL_LOCAL_CONTRADICTION")

    def test_raw_option_oi_never_votes(self):
        family = option_participation_family(_board())
        self.assertEqual(family["stance"], "UNKNOWN")
        self.assertFalse(family["counts_for_direction"])
        self.assertEqual(family["state"], "RAW_OPTION_POSITIONING_CONTEXT_ONLY")

    def test_absolute_china_macro_level_never_votes(self):
        family = china_demand_family(_board())
        self.assertEqual(family["stance"], "UNKNOWN")
        self.assertFalse(family["counts_for_direction"])
        self.assertEqual(family["state"], "SLOW_MACRO_CONTEXT_ONLY")

    def test_one_independent_family_is_not_enough_for_direction(self):
        result = evaluate_copper_direction_v2_shadow(_board())
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["direction_confidence"], "WEAK")
        self.assertEqual(result["thesis_state"], "INSUFFICIENT_INDEPENDENT_CONFIRMATION")
        self.assertEqual(result["supporting_families"], ["LOCAL_STRUCTURE"])
        self.assertEqual(result["decision_effect"], "NONE")
        self.assertFalse(result["setup_geometry_generated"])
        self.assertFalse(result["option_expression_generated"])

    def test_two_distinct_aligned_causal_origins_can_form_shadow_thesis(self):
        thesis = _thesis([
            {
                "family": "LOCAL_STRUCTURE",
                "causal_origin": "LOCAL_PRICE_STRUCTURE",
                "stance": "BEARISH",
                "counts_for_direction": True,
            },
            {
                "family": "EVENT_REACTION",
                "causal_origin": "COPPER_EVENT_CAUSAL_REACTION",
                "stance": "BEARISH",
                "counts_for_direction": True,
            },
        ])
        self.assertEqual(thesis["direction"], "BEARISH")
        self.assertEqual(thesis["confidence"], "MODERATE")
        self.assertEqual(thesis["state"], "COHERENT_DIRECTION_THESIS")

    def test_opposed_independent_origins_force_unknown(self):
        thesis = _thesis([
            {
                "family": "LOCAL_STRUCTURE",
                "causal_origin": "LOCAL_PRICE_STRUCTURE",
                "stance": "BULLISH",
                "counts_for_direction": True,
            },
            {
                "family": "GLOBAL_COPPER",
                "causal_origin": "GLOBAL_COPPER_PRICE_DISCOVERY",
                "stance": "BEARISH",
                "counts_for_direction": True,
            },
        ])
        self.assertEqual(thesis["direction"], "UNKNOWN")
        self.assertEqual(thesis["confidence"], "CONFLICTED")

    def test_duplicate_causal_origin_is_not_double_counted(self):
        thesis = _thesis([
            {
                "family": "A",
                "causal_origin": "SAME_ORIGIN",
                "stance": "BULLISH",
                "counts_for_direction": True,
            },
            {
                "family": "B",
                "causal_origin": "SAME_ORIGIN",
                "stance": "BULLISH",
                "counts_for_direction": True,
            },
        ])
        self.assertEqual(thesis["direction"], "UNKNOWN")
        self.assertEqual(thesis["state"], "INSUFFICIENT_INDEPENDENT_CONFIRMATION")
        self.assertEqual(thesis["duplicate_causal_origins_suppressed"], ["SAME_ORIGIN"])

    def test_integration_contract_keeps_all_execution_and_promotion_off(self):
        contract = integration_contract()
        self.assertEqual(contract["current_mind_effect"], "NONE")
        self.assertEqual(contract["geometry_effect"], "NONE")
        self.assertEqual(contract["option_expression_effect"], "NONE")
        self.assertFalse(contract["raw_option_oi_directional_vote_allowed"])
        self.assertFalse(contract["absolute_macro_level_directional_vote_allowed"])
        self.assertFalse(contract["headline_sentiment_direction_allowed"])
        self.assertFalse(contract["live_execution_enabled"])
        self.assertFalse(contract["broker_order_placement_enabled"])
        self.assertFalse(contract["promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
