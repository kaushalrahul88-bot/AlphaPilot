from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_oil_mini_no_news_brain import (
    _resolve_geometry,
    build_snapshot,
    decide_no_news,
    deterministic_mini_clicks,
)

IST = ZoneInfo("Asia/Kolkata")


def session(day="2026-08-20", bars=174, start_hour=9, base=8200.0):
    start = datetime.fromisoformat(f"{day}T{start_hour:02d}:00:00").replace(tzinfo=IST)
    rows = []
    price = base
    for index in range(bars):
        stamp = start + timedelta(minutes=5 * index)
        close = price + ((index % 7) - 3) * 0.4
        high = max(price, close) + 1.0
        low = min(price, close) - 1.0
        rows.append([stamp.isoformat(), price, high, low, close, 100 + index])
        price = close
    return rows


class CrudeOilMiniNoNewsBrainTests(unittest.TestCase):
    def test_click_schedule_is_20_timestamp_only_clicks_between_10_and_22(self):
        rows = session()
        clicks_a = deterministic_mini_clicks(rows)
        clicks_b = deterministic_mini_clicks(rows)
        self.assertEqual(clicks_a, clicks_b)
        self.assertEqual(len(clicks_a), 20)
        for click in clicks_a:
            stamp = datetime.fromisoformat(click["click_timestamp"]).astimezone(IST)
            self.assertGreaterEqual((stamp.hour, stamp.minute), (10, 0))
            self.assertLessEqual((stamp.hour, stamp.minute), (22, 0))
            self.assertEqual(click["bar_visibility_policy"], "BAR_START_PLUS_5_MINUTES")

    def test_bar_starting_at_click_is_not_visible(self):
        rows = session()
        click = datetime.fromisoformat("2026-08-20T10:00:00+05:30")
        for row in rows:
            if row[0].startswith("2026-08-20T09:55:00"):
                row[4] = 8100.0
                row[2] = max(row[2], 8101.0)
                row[3] = min(row[3], 8099.0)
            if row[0].startswith("2026-08-20T10:00:00"):
                row[4] = 9000.0
                row[2] = 9001.0
                row[3] = min(row[3], 8999.0)
        snapshot = build_snapshot(rows, click)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["latest_visible_bar_start"], "2026-08-20T09:55:00+05:30")
        self.assertEqual(snapshot["price"], 8100.0)

    def test_future_mutation_cannot_change_point_in_time_snapshot(self):
        rows = session()
        click = datetime.fromisoformat("2026-08-20T11:00:00+05:30")
        before = build_snapshot(rows, click)
        mutated = [list(row) for row in rows]
        for row in mutated:
            if datetime.fromisoformat(row[0]) >= click:
                row[1:5] = [20000.0, 21000.0, 19000.0, 20500.0]
                row[5] = 9999999
        after = build_snapshot(mutated, click)
        self.assertEqual(before, after)

    def test_three_bullish_lanes_with_price_confirmation_can_buy_ce(self):
        features = {
            "return_15m_pct": 0.12,
            "return_30m_pct": 0.20,
            "return_60m_pct": 0.30,
            "structure": "UPTREND",
            "session_vwap_gap_pct": 0.15,
            "session_range_position": 0.75,
            "relative_volume": 0.9,
            "breakout": "NONE",
        }
        memory = {"lane": "MEMORY", "stance": "UNKNOWN"}
        decision = decide_no_news(features, memory)
        self.assertEqual(decision["action"], "BUY_CE")
        self.assertFalse(decision["news_used"])

    def test_same_bar_target_and_stop_is_scored_conservatively_as_stop(self):
        click = datetime.fromisoformat("2026-08-20T10:00:00+05:30")
        rows = [[click.isoformat(), 100.0, 104.0, 96.0, 101.0, 100.0]]
        geometry = {"entry": 100.0, "stop": 98.0, "target": 103.0, "risk_points": 2.0, "target_r": 1.5}
        result = _resolve_geometry(rows, click, "BUY_CE", geometry)
        self.assertEqual(result["result"], "STOP")
        self.assertTrue(result["same_bar_ambiguous"])
        self.assertEqual(result["realized_r"], -1.0)


if __name__ == "__main__":
    unittest.main()
