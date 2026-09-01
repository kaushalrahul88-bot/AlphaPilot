from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_oil_mini_data_probe import (
    _complete_sessions,
    _nearest_option,
    _sample_days,
)

IST = ZoneInfo("Asia/Kolkata")


class CrudeOilMiniDataProbeTests(unittest.TestCase):
    def test_complete_session_requires_late_coverage_and_enough_bars(self):
        start = datetime(2026, 8, 31, 9, 0, tzinfo=IST)
        rows = []
        for index in range(170):
            stamp = start + timedelta(minutes=5 * index)
            rows.append([stamp.isoformat(), 8200, 8202, 8198, 8201, 10])
        sessions = _complete_sessions(rows)
        self.assertEqual(len(sessions), 1)
        self.assertTrue(sessions[0]["complete_for_20_click_research"])

    def test_short_day_is_not_called_complete(self):
        start = datetime(2026, 8, 31, 9, 0, tzinfo=IST)
        rows = [
            [(start + timedelta(minutes=5 * index)).isoformat(), 8200, 8202, 8198, 8201, 10]
            for index in range(80)
        ]
        self.assertFalse(_complete_sessions(rows)[0]["complete_for_20_click_research"])

    def test_nearest_option_stays_inside_mini_expiry_and_type(self):
        rows = [
            {"underlying":"CRUDEOILM","instrument_type":"CE","expiry":"2026-09-17","strike":8150},
            {"underlying":"CRUDEOILM","instrument_type":"CE","expiry":"2026-09-17","strike":8250},
            {"underlying":"CRUDEOILM","instrument_type":"PE","expiry":"2026-09-17","strike":8200},
            {"underlying":"CRUDEOILM","instrument_type":"CE","expiry":"2026-10-15","strike":8200},
        ]
        selected = _nearest_option(rows, expiry="2026-09-17", option_type="CE", price=8210)
        self.assertEqual(selected["strike"], 8250)
        self.assertEqual(selected["expiry"], "2026-09-17")

    def test_sample_days_are_earliest_middle_latest_complete(self):
        sessions = [
            {"date":f"2026-08-{day:02d}","complete_for_20_click_research":True}
            for day in range(1, 10)
        ]
        sample = _sample_days(sessions)
        self.assertEqual([x.isoformat() for x in sample], ["2026-08-01", "2026-08-05", "2026-08-09"])


if __name__ == "__main__":
    unittest.main()
