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
        "time_adjusted_relative_volume": 1.6,
    }


def _profile() -> dict:
    return {"participation_confirming": 1.2}


def _record(series: str, stance: str) -> dict:
    return {
        "series": series,
        "observed_at": "2026-09-03T13:00:00+05:30",
        "available_at": "2026-09-03T13:05:00+05:30",
        "stance": stance,
        "value": {"stance": stance},
        "source": "synthetic_test",
    }


def _event_record(*, mechanism: str, reaction: str, confirmed: bool, materiality: str = "MATERIAL") -> dict:
    return {
        "series": "CRUDE_NEWS",
        "event_id": "synthetic_event",
        "event_type": "SUPPLY_DISRUPTION",
        "observed_at": "2026-09-03T13:00:00+05:30",
        "available_at": "2026-09-03T13:05:00+05:30",
        "value": {
            "mechanism_stance": mechanism,
            "materiality_status": materiality,
            "novelty_status": "NEW",
            "reaction": {
                "direction": reaction,
                "confirmed": confirmed,
                "confirmation_sources": ["WTI_CRUDE"],
            },
        },
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
        self.assertFalse(result["families"]["PARTICIPATION"]["counts_for_direction"])
        self.assertEqual(result["families"]["PARTICIPATION"]["state"], "LEGACY_PRICE_VOLUME_PROXY_SUPPRESSED")
        self.assertFalse(result["decision_path_changed"])
        self.assertIsNone(result["current_mind_action"])
        self.assertFalse(result["geometry_generated"])
        self.assertFalse(result["promotion_allowed"])

    def test_price_volume_proxy_cannot_double_count_local_momentum(self):
        result = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot(),
            profile=_profile(),
            context_records=[],
            direction_memory_cases=[],
        )
        participation = result["families"]["PARTICIPATION"]
        self.assertTrue(participation["detail"]["legacy_proxy_would_have_voted"])
        self.assertEqual(participation["independence_status"], "DEPENDENT_ON_LOCAL_PRICE")
        self.assertFalse(participation["counts_for_direction"])
        self.assertEqual(result["direction"], "UNKNOWN")

    def test_independent_commitment_participation_can_vote(self):
        observation = {
            "family": "PARTICIPATION",
            "causal_origin": "POSITIONING_FLOW",
            "independence_status": "INDEPENDENT",
            "depends_on": [],
            "counts_for_direction": True,
            "stance": "BULLISH",
            "state": "INITIATIVE_BUYING",
            "detail": {"oi_delta": 100.0, "accepted_above_prior_range": True},
        }
        result = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=_snapshot(),
            profile=_profile(),
            context_records=[],
            direction_memory_cases=[],
            participation_observation=observation,
        )
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["supporting_families"], ["LOCAL_STRUCTURE", "PARTICIPATION"])
        self.assertEqual(result["dependency_audit"]["counted_origins"], ["LOCAL_PRICE_STRUCTURE", "POSITIONING_FLOW"])

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
        self.assertEqual(result["dependency_audit"]["counted_origins"], ["CROSS_MARKET_CRUDE"])

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
        self.assertEqual(result["thesis_state"], "INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION")

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

    def test_event_requires_mechanism_materiality_and_confirmed_reaction(self):
        flat = {"structure": "RANGE", "return_15m_pct": 0.0, "return_60m_pct": 0.0}
        unconfirmed = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=flat,
            profile=_profile(),
            context_records=[_event_record(mechanism="BULLISH", reaction="BULLISH", confirmed=False)],
            direction_memory_cases=[],
        )
        confirmed = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=flat,
            profile=_profile(),
            context_records=[_event_record(mechanism="BULLISH", reaction="BULLISH", confirmed=True)],
            direction_memory_cases=[],
        )
        self.assertFalse(unconfirmed["families"]["EVENT_REACTION"]["counts_for_direction"])
        self.assertTrue(confirmed["families"]["EVENT_REACTION"]["counts_for_direction"])
        self.assertEqual(confirmed["families"]["EVENT_REACTION"]["state"], "CONFIRMED_BULLISH")
        self.assertEqual(confirmed["direction"], "UNKNOWN")

    def test_event_rejection_does_not_create_reverse_vote(self):
        flat = {"structure": "RANGE", "return_15m_pct": 0.0, "return_60m_pct": 0.0}
        result = brain.evaluate_direction_brain_v2_shadow(
            click_timestamp=CLICK,
            snapshot=flat,
            profile=_profile(),
            context_records=[_event_record(mechanism="BULLISH", reaction="BEARISH", confirmed=True)],
            direction_memory_cases=[],
        )
        event = result["families"]["EVENT_REACTION"]
        self.assertEqual(event["state"], "BULLISH_EVENT_REJECTED")
        self.assertEqual(event["stance"], "UNKNOWN")
        self.assertFalse(event["counts_for_direction"])

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
        self.assertTrue(contract["causal_origin_deduplication"])
        self.assertTrue(contract["legacy_participation_price_vote_suppressed"])


if __name__ == "__main__":
    unittest.main()
