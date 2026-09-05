import unittest
from datetime import datetime, timezone

from app.crypto_btc_missed_move_postmortem import (
    BtcMissedMoveFinding,
    architecture_contract,
    build_missed_move_postmortem,
)


def _t(hour=4, minute=0):
    return datetime(2026, 9, 5, hour, minute, tzinfo=timezone.utc)


def _entry(*, missed=True, available=None, missing=None):
    return {
        "version": "BTC_RANDOM_CLICK_EXPERIENCE_V1",
        "click_id": "btc-click-1",
        "asset": "BTC",
        "instrument_type": "OPTIONS",
        "decision_at": _t().isoformat(),
        "final_decision": "NO_TRADE",
        "outcome_type": "NO_TRADE_LEARNING",
        "reason_codes": ["INSUFFICIENT_INDEPENDENT_CONFIRMATION"],
        "available_lanes": available or ["BTC_SPOT_STRUCTURE", "DERIVATIVES"],
        "missing_lanes": missing or ["NEWS", "ONCHAIN"],
        "no_trade_follow_through": {
            "status": "NO_TRADE_FOLLOW_THROUGH_RESOLVED",
            "classification": "MISSED_LARGE_MOVE_UP" if missed else "NO_LARGE_MOVE_AFTER_NO_TRADE",
            "large_move_missed": missed,
            "missed_direction": "UP" if missed else None,
            "max_abs_move_pct": 4.5 if missed else 1.0,
        },
        "futures_route_invoked": False,
        "futures_trade_generated": False,
    }


def _finding(family="DERIVATIVES_POSITIONING", first_seen=None, confidence=0.9, **overrides):
    values = dict(
        family=family,
        first_seen_at=first_seen or _t(3, 55),
        summary="Material evidence relevant to the subsequent BTC move.",
        confidence=confidence,
        independent_source_count=2,
        source_tier="HIGH_QUALITY_MARKET_DATA",
        verified=True,
        material_to_move=True,
    )
    values.update(overrides)
    return BtcMissedMoveFinding(**values)


class BtcMissedMovePostmortemTests(unittest.TestCase):
    def test_postmortem_not_required_without_large_move(self):
        result = build_missed_move_postmortem(experience_entry=_entry(missed=False), findings=[])
        self.assertEqual(result["status"], "POSTMORTEM_NOT_REQUIRED")
        self.assertFalse(result["historical_decision_rewritten"])

    def test_available_precick_signal_is_underweighted_or_gated_hypothesis(self):
        result = build_missed_move_postmortem(
            experience_entry=_entry(available=["BTC_SPOT_STRUCTURE", "DERIVATIVES"]),
            findings=[_finding()],
        )
        self.assertEqual(result["primary_learning_classification"], "POTENTIAL_UNDERWEIGHTED_OR_GATED_SIGNAL")
        self.assertEqual(result["available_at_click_material_count"], 1)
        self.assertIn("WEIGHTING", result["investigation_dimensions"])
        self.assertFalse(result["automatic_strategy_change_allowed"])

    def test_missing_lane_precick_signal_is_data_coverage_gap_hypothesis(self):
        result = build_missed_move_postmortem(
            experience_entry=_entry(available=["BTC_SPOT_STRUCTURE"], missing=["NEWS", "ONCHAIN"]),
            findings=[_finding(family="NEWS", first_seen=_t(3, 50))],
        )
        self.assertEqual(result["primary_learning_classification"], "POTENTIAL_DATA_COVERAGE_GAP")
        self.assertEqual(result["missing_at_click_material_count"], 1)
        self.assertIn("DATA_COVERAGE", result["investigation_dimensions"])

    def test_postclick_news_is_not_counted_as_available_at_click(self):
        result = build_missed_move_postmortem(
            experience_entry=_entry(),
            findings=[_finding(family="NEWS", first_seen=_t(4, 10))],
        )
        self.assertEqual(result["primary_learning_classification"], "LIKELY_POST_CLICK_CATALYST")
        self.assertEqual(result["post_click_material_count"], 1)
        self.assertEqual(result["available_at_click_material_count"], 0)
        self.assertFalse(result["historical_decision_rewritten"])

    def test_low_confidence_finding_does_not_drive_primary_learning_classification(self):
        result = build_missed_move_postmortem(
            experience_entry=_entry(),
            findings=[_finding(confidence=0.4)],
            min_actionable_confidence=0.7,
        )
        self.assertEqual(result["primary_learning_classification"], "CAUSE_NOT_ESTABLISHED")
        self.assertEqual(result["high_confidence_material_finding_count"], 0)

    def test_unverified_finding_does_not_drive_classification(self):
        result = build_missed_move_postmortem(
            experience_entry=_entry(),
            findings=[_finding(verified=False)],
        )
        self.assertEqual(result["primary_learning_classification"], "CAUSE_NOT_ESTABLISHED")

    def test_available_signal_takes_priority_over_postclick_catalyst_for_investigation(self):
        result = build_missed_move_postmortem(
            experience_entry=_entry(available=["BTC_SPOT_STRUCTURE", "DERIVATIVES"]),
            findings=[
                _finding(),
                _finding(family="NEWS", first_seen=_t(4, 10)),
            ],
        )
        self.assertEqual(result["primary_learning_classification"], "POTENTIAL_UNDERWEIGHTED_OR_GATED_SIGNAL")
        self.assertEqual(result["available_at_click_material_count"], 1)
        self.assertEqual(result["post_click_material_count"], 1)

    def test_unknown_click_time_lane_availability_is_preserved_as_uncertain(self):
        result = build_missed_move_postmortem(
            experience_entry=_entry(available=["BTC_SPOT_STRUCTURE"], missing=["NEWS"]),
            findings=[_finding(family="STABLECOIN")],
        )
        self.assertEqual(result["primary_learning_classification"], "CLICK_TIME_AVAILABILITY_UNCERTAIN")
        self.assertEqual(result["availability_uncertain_material_count"], 1)

    def test_non_no_trade_experience_is_rejected(self):
        entry = _entry()
        entry["final_decision"] = "BUY_CALL"
        with self.assertRaises(ValueError):
            build_missed_move_postmortem(experience_entry=entry, findings=[])

    def test_futures_route_state_is_rejected(self):
        entry = _entry()
        entry["futures_route_invoked"] = True
        with self.assertRaises(ValueError):
            build_missed_move_postmortem(experience_entry=entry, findings=[])

    def test_findings_are_classified_without_rewriting_decision(self):
        result = build_missed_move_postmortem(
            experience_entry=_entry(available=["BTC_SPOT_STRUCTURE", "DERIVATIVES"]),
            findings=[_finding()],
        )
        finding = result["findings"][0]
        self.assertEqual(finding["availability_classification"], "AVAILABLE_AT_CLICK")
        self.assertFalse(finding["historical_decision_rewritten"])
        self.assertEqual(result["recommendation_status"], "HYPOTHESIS_ONLY_REQUIRES_OUT_OF_SAMPLE_TEST")
        self.assertFalse(result["automatic_weight_change_allowed"])
        self.assertFalse(result["automatic_gate_change_allowed"])

    def test_architecture_contract_requires_out_of_sample_retest(self):
        contract = architecture_contract()
        self.assertTrue(contract["preserves_frozen_historical_decision"])
        self.assertTrue(contract["postclick_catalyst_cannot_be_counted_as_available_at_click"])
        self.assertFalse(contract["automatic_retuning_allowed"])
        self.assertTrue(contract["out_of_sample_retest_required_before_strategy_change"])
        self.assertFalse(contract["futures_trade_generation_allowed"])
        self.assertFalse(contract["broker_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
