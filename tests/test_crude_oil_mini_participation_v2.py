from __future__ import annotations

import unittest

from app.crude_oil_mini_participation_v2 import build_participation_observation, participation_contract

CLICK = "2026-09-03T14:30:00+05:30"


def _candles(*, oi: bool = True, rising_oi: bool = True):
    base_oi = [1000, 1010, 1020, 1030, 1080, 1120] if rising_oi else [1120, 1100, 1080, 1060, 1040, 1020]
    values = [
        ["2026-09-03T14:00:00+05:30", 100.0, 100.5, 99.5, 100.0, 100.0],
        ["2026-09-03T14:05:00+05:30", 100.0, 100.7, 99.8, 100.4, 110.0],
        ["2026-09-03T14:10:00+05:30", 100.4, 101.0, 100.2, 100.8, 120.0],
        ["2026-09-03T14:15:00+05:30", 100.8, 101.2, 100.6, 101.0, 130.0],
        ["2026-09-03T14:20:00+05:30", 101.0, 102.0, 100.9, 101.7, 220.0],
        ["2026-09-03T14:25:00+05:30", 101.7, 103.0, 101.5, 102.6, 320.0],
    ]
    if oi:
        for row, value in zip(values, base_oi):
            row.append(value)
    return values


class CrudeOilMiniParticipationV2Tests(unittest.TestCase):
    def test_fresh_oi_plus_acceptance_can_form_initiative_buying(self):
        result = build_participation_observation(
            _candles(),
            click_timestamp=CLICK,
            snapshot={"time_adjusted_relative_volume": 1.6, "session_vwap_gap_pct": 0.5},
            profile={"participation_confirming": 1.2},
        )
        self.assertEqual(result["state"], "INITIATIVE_BUYING")
        self.assertEqual(result["stance"], "BULLISH")
        self.assertTrue(result["counts_for_direction"])
        self.assertEqual(result["causal_origin"], "POSITIONING_FLOW")
        self.assertEqual(result["independence_status"], "INDEPENDENT")

    def test_price_volume_without_oi_is_dependent_and_cannot_vote(self):
        result = build_participation_observation(
            _candles(oi=False),
            click_timestamp=CLICK,
            snapshot={"time_adjusted_relative_volume": 1.6, "session_vwap_gap_pct": 0.5},
            profile={"participation_confirming": 1.2},
        )
        self.assertEqual(result["state"], "PRICE_VOLUME_ONLY_DEPENDENT")
        self.assertEqual(result["independence_status"], "DEPENDENT_ON_LOCAL_PRICE")
        self.assertFalse(result["counts_for_direction"])

    def test_falling_oi_is_position_closure_not_fresh_bullish_commitment(self):
        result = build_participation_observation(
            _candles(rising_oi=False),
            click_timestamp=CLICK,
            snapshot={"time_adjusted_relative_volume": 1.6, "session_vwap_gap_pct": 0.5},
            profile={"participation_confirming": 1.2},
        )
        self.assertEqual(result["state"], "SHORT_COVERING")
        self.assertFalse(result["counts_for_direction"])

    def test_contract_keeps_current_mind_untouched(self):
        contract = participation_contract()
        self.assertEqual(contract["current_mind_effect"], "NONE")
        self.assertFalse(contract["price_volume_only_can_vote"])
        self.assertFalse(contract["threshold_search_on_inspected_august_allowed"])


if __name__ == "__main__":
    unittest.main()
