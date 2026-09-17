from __future__ import annotations

import unittest
from datetime import timedelta

from app.commodity_time import parse_ist_timestamp
from app.crude_oil_mini_sep_3day_15m_replay import (
    CLICKS_PER_DAY,
    EVALUATION_DAYS,
    EXPECTED_CLICKS,
    _click_schedule,
    replay_contract,
)


class CrudeOilMiniSepThreeDayReplayTests(unittest.TestCase):
    def test_schedule_has_49_clicks_per_day_and_147_total(self):
        rows = _click_schedule()
        self.assertEqual(EXPECTED_CLICKS, 147)
        self.assertEqual(len(rows), 147)
        for day in EVALUATION_DAYS:
            day_rows = [row for row in rows if row["session"] == day]
            self.assertEqual(len(day_rows), CLICKS_PER_DAY)
            self.assertEqual(parse_ist_timestamp(day_rows[0]["click_timestamp"]).strftime("%H:%M"), "10:00")
            self.assertEqual(parse_ist_timestamp(day_rows[-1]["click_timestamp"]).strftime("%H:%M"), "22:00")

    def test_schedule_is_exactly_15_minutes_apart_inside_each_day(self):
        rows = _click_schedule()
        for day in EVALUATION_DAYS:
            stamps = [
                parse_ist_timestamp(row["click_timestamp"])
                for row in rows
                if row["session"] == day
            ]
            self.assertTrue(all(b - a == timedelta(minutes=15) for a, b in zip(stamps, stamps[1:])))

    def test_contract_keeps_integrated_v2_shadow_only(self):
        contract = replay_contract()
        self.assertTrue(contract["integrated_v2_shadow_only"])
        self.assertFalse(contract["current_mind_rules_changed"])
        self.assertFalse(contract["fixed_horizon_direction_scoring_used"])
        self.assertFalse(contract["threshold_search_used"])
        self.assertFalse(contract["parameter_optimization_used"])
        self.assertFalse(contract["synthetic_option_prices_used"])
        self.assertFalse(contract["regular_crude_used"])
        self.assertFalse(contract["missing_futures_oi_reconstructed"])


if __name__ == "__main__":
    unittest.main()
