from __future__ import annotations

import inspect
import unittest

from app import crude_oil_mini_direction_brain_v2 as brain

CLICK = "2026-09-03T14:00:00+05:30"


def _snapshot() -> dict:
    return {
        "structure": "UPTREND",
        "return_15m_pct": 0.20,
        "return_60m_pct": 0.35,
        "time_adjusted_relative_volume": 0.8,
    }


def _profile() -> dict:
    return {"participation_confirming": 1.2}


def _record(series: str, stance: str, *, confirmed: bool = False) -> dict:
    return {
        "series": series,
        "observed_at": "2026-09-03T13:00:00+05:30",
        "available_at": "2026-09-03T13:05:00+05:30",
        "stance": stance,
        "reaction_confirmed": confirmed,
        "value": {"stance": stance, "reaction_confirmed": confirmed},
        "source": "synthetic_test",
    }


class CrudeOilMiniDirectionBrainV2Tests(unittest.TestCase):
    def test_two_independent_families_can_form_shadow_direction(self):
        result = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot(),
            profile=_profile(),
            context_records=[_record("WTI_CRUDE", "BULLISH"), _record("BRENT_CRUDE", "BULLISH")],
            direction_memory_cases=[],
        )
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["direction_confidence"], "MODERATE")
        self.assertEqual(result["supporting_families"], ["GLOBAL_CRUDE", "LOCAL_STRUCTURE"])
        self.assertFalse(result["decision_path_changed"])
        self.assertIsNone(result["current_mind_action"])
        self.assertFalse(result["geometry_generated"])
        self.assertFalse(result["promotion_allowed"])

    def test_wti_and_brent_are_one_family_not_two_votes(self):
        flat_snapshot = {"structure": "RANGE", "return_15m_pct": 0.0, "return_60m_pct": 0.0}
        result = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=flat_snapshot,
            profile=_profile(),
            context_records=[_record("WTI_CRUDE", "BULLISH"), _record("BRENT_CRUDE", "BULLISH")],
            direction_memory_cases=[],
        )
        self.assertEqual(result["families"]["GLOBAL_CRUDE"]["state"], "WTI_BRENT_CONFIRMED")
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["thesis_state"], "INSUFFICIENT_INDEPENDENT_CONFIRMATION")

    def test_independent_contradiction_forces_abstention(self):
        result = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot(),
            profile=_profile(),
            context_records=[_record("WTI_CRUDE", "BEARISH"), _record("BRENT_CRUDE", "BEARISH")],
            direction_memory_cases=[],
        )
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["direction_confidence"], "CONFLICTED")
        self.assertEqual(result["thesis_state"], "INDEPENDENT_FAMILY_CONTRADICTION")

    def test_usdinr_is_modifier_only_and_cannot_reverse_direction(self):
        result = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot(),
            profile=_profile(),
            context_records=[
                _record("WTI_CRUDE", "BULLISH"),
                _record("BRENT_CRUDE", "BULLISH"),
                _record("USDINR", "BEARISH"),
            ],
            direction_memory_cases=[],
        )
        self.assertEqual(result["direction"], "BULLISH")
        fx = result["modifiers"]["FX_TRANSLATION"]
        self.assertEqual(fx["state"], "OPPOSES_GLOBAL_CRUDE")
        self.assertFalse(fx["counts_for_direction"])

    def test_event_needs_explicit_price_reaction_confirmation(self):
        unconfirmed = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot={"structure": "RANGE", "return_15m_pct": 0.0, "return_60m_pct": 0.0},
            profile=_profile(),
            context_records=[_record("CRUDE_NEWS", "BULLISH", confirmed=False)],
            direction_memory_cases=[],
        )
        confirmed = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot={"structure": "RANGE", "return_15m_pct": 0.0, "return_60m_pct": 0.0},
            profile=_profile(),
            context_records=[_record("CRUDE_NEWS", "BULLISH", confirmed=True)],
            direction_memory_cases=[],
        )
        self.assertFalse(unconfirmed["families"]["EVENT_REACTION"]["counts_for_direction"])
        self.assertTrue(confirmed["families"]["EVENT_REACTION"]["counts_for_direction"])
        self.assertEqual(confirmed["direction"], "UNKNOWN")

    def test_preregistration_blocks_in_sample_promotion_and_geometry_tuning(self):
        contract = brain.preregistration_contract()
        self.assertEqual(contract["development_sample_end"], "2026-08-31")
        self.assertEqual(contract["prospective_validation_not_before"], "2026-09-03")
        self.assertFalse(contract["geometry_tuning_allowed"])
        self.assertFalse(contract["threshold_search_on_june_august_allowed"])
        self.assertFalse(contract["promotion_allowed_from_june_august"])

    def test_shadow_module_cannot_call_current_mind_or_option_brain(self):
        source = inspect.getsource(brain)
        self.assertNotIn("build_current_mind_decision", source)
        self.assertNotIn("crude_oil_mini_current_mind_click", source)
        self.assertNotIn("option_brain", source.lower().replace('"option_brain_action"', ''))
        contract = brain.architecture_contract()
        self.assertEqual(contract["decision_effect"], "NONE")
        self.assertEqual(contract["geometry_effect"], "NONE")
        self.assertEqual(contract["option_effect"], "NONE")


if __name__ == "__main__":
    unittest.main()
