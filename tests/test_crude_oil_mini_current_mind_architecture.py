from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import crude_oil_mini_current_mind as integrated
from app import crude_oil_mini_experience_memory as experience
from app import crude_oil_mini_market_perception as perception
from app import current_mind_crude_oil_mini_replay as replay

IST = ZoneInfo("Asia/Kolkata")


def _session(day: int, *, start_price: float = 8000.0, drift: float = 0.25):
    start = datetime(2026, 8, day, 9, 0, tzinfo=IST)
    rows = []
    price = start_price
    for i in range(160):
        stamp = start + timedelta(minutes=5 * i)
        o = price
        c = price + drift + ((i % 7) - 3) * 0.03
        h = max(o, c) + 0.8
        l = min(o, c) - 0.8
        volume = 1000 + (i % 20) * 15
        rows.append([stamp.isoformat(), o, h, l, c, volume])
        price = c
    return rows


def _memory_candidate(index: int, value: float) -> dict:
    return {
        "id": index,
        "resolved_at": f"2026-08-03T{10 + index // 12:02d}:{(index % 12) * 5:02d}:00+05:30",
        "direction": "BULLISH" if index % 2 == 0 else "BEARISH",
        "vector": {key: value for key in experience.FEATURES},
        "structure": "UPTREND",
        "opening_range_break": "INSIDE",
        "price_oi_state": "UNKNOWN",
        "outcome": "TARGET_FIRST" if index % 3 == 0 else "STOP_FIRST",
        "mfe_pct": 0.1,
        "mae_pct": 0.05,
    }


class CrudeOilMiniCurrentMindArchitectureTests(unittest.TestCase):
    def test_bar_is_visible_only_after_completion(self):
        rows, _ = perception.precompute_perception(_session(3))
        bar = rows[12]
        start = datetime.fromisoformat(bar[0])
        self.assertLess(perception.latest_visible_index(rows, start), 12)
        self.assertEqual(perception.latest_visible_index(rows, start + timedelta(minutes=5)), 12)

    def test_current_day_cannot_change_its_own_profile(self):
        day1 = _session(3, start_price=8000, drift=0.15)
        day2 = _session(4, start_price=8100, drift=0.20)
        rows_a, features_a = perception.precompute_perception(day1 + day2)
        profile_a = perception.causal_profiles(rows_a, features_a)["2026-08-04"]

        altered_day2 = _session(4, start_price=8100, drift=4.0)
        rows_b, features_b = perception.precompute_perception(day1 + altered_day2)
        profile_b = perception.causal_profiles(rows_b, features_b)["2026-08-04"]
        self.assertEqual(profile_a, profile_b)

    def test_decision_memory_withholds_unresolved_cases(self):
        click = "2026-08-04T12:00:00+05:30"
        cases = [
            {"available_at": "2026-08-04T11:55:00+05:30", "regime": {}, "evidence": {}},
            {"available_at": "2026-08-04T12:05:00+05:30", "regime": {}, "evidence": {}},
            {"regime": {}, "evidence": {}},
        ]
        visible = integrated._visible_memory_cases(cases, click)
        self.assertEqual(len(visible), 1)

    def test_memory_top_k_runtime_path_matches_original_stable_sort(self):
        # Repeated values deliberately create equal-distance candidates. The optimized
        # heap path must preserve the source-order tie semantics of Python's stable sort.
        candidates = [
            _memory_candidate(i, value)
            for i, value in enumerate((0.0, 0.5, 0.5, 1.0, 0.5, -0.5, -0.5, 2.0, 0.0, 0.5))
        ]
        query = {
            "vector": {key: 0.0 for key in experience.FEATURES},
            "structure": "UPTREND",
            "opening_range_break": "INSIDE",
            "price_oi_state": "UNKNOWN",
        }
        scales = {key: 1.0 for key in experience.FEATURES}
        expected = sorted(
            candidates,
            key=lambda row: experience._distance(query, row, scales),
        )[:6]
        actual = experience._stable_nearest(candidates, query, scales, 6)
        self.assertEqual([row["id"] for row in actual], [row["id"] for row in expected])

    def test_experience_memory_resolved_at_cutoff_remains_strict(self):
        click = "2026-08-04T12:00:00+05:30"
        candidates = []
        for i in range(39):
            row = _memory_candidate(i, float(i % 5))
            row["resolved_at"] = "2026-08-04T11:55:00+05:30"
            candidates.append(row)
        equal = _memory_candidate(39, 0.0)
        equal["resolved_at"] = click
        candidates.append(equal)
        snapshot = {
            **{key: 0.0 for key in experience.FEATURES},
            "structure": "UPTREND",
            "opening_range_break": "INSIDE",
            "price_oi_state": "UNKNOWN",
        }
        result = experience.query_memory(candidates, snapshot, click)
        self.assertEqual(result["status"], "INSUFFICIENT_MEMORY")
        self.assertEqual(result["prior_resolved_experiences"], 39)

    def test_crude_specific_layers_have_no_copper_import(self):
        for module in (perception, experience, integrated, replay):
            source = inspect.getsource(module)
            self.assertNotIn("from .copper", source)
            self.assertNotIn("import copper", source)

    def test_replay_uses_twenty_clicks_per_complete_crude_session(self):
        candles = _session(3, start_price=8000, drift=0.20) + _session(4, start_price=8040, drift=-0.12) + _session(5, start_price=8020, drift=0.10)
        report = replay.evaluate_crude_oil_mini_current_mind_no_news(
            candles,
            {"trading_symbol": "CRUDEOILM21SEP26FUT", "lot_size": 10},
        )
        self.assertEqual(report["complete_sessions"], 3)
        self.assertEqual(report["scheduled_clicks"], 60)
        self.assertEqual(report["evaluated_clicks"], 60)
        self.assertTrue(report["click_coverage_exact"])
        self.assertFalse(report["news_enabled"])
        self.assertFalse(report["option_market_data_used"])
        self.assertFalse(report["integrity"]["copper_data_used"])
        for row in report["decisions"]:
            click = datetime.fromisoformat(row["click_timestamp"])
            visible_at = datetime.fromisoformat(row["latest_visible_bar_available_at"])
            self.assertLessEqual(visible_at, click)
            self.assertIn(row["action"], {"BUY_CE", "BUY_PE", "WAIT"})


if __name__ == "__main__":
    unittest.main()
