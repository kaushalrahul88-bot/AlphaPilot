from __future__ import annotations

import copy
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.crude_oil_mini_prospective_memory_v1 import (
    MAX_ANALOGUES,
    MIN_READY_CASES,
    _load_cases_sync,
    summarize_prospective_memory,
)
from app.crude_oil_mini_research_protocol_v1 import PRIMARY_OUTCOME_HORIZON_MINUTES


IST = ZoneInfo("Asia/Kolkata")


def _current() -> dict:
    return {
        "journal": {
            "regime": {"regime_labels": ["TREND", "EXPANDING"]},
            "evidence": {
                "independent_bullish_lanes": ["STRUCTURE", "PARTICIPATION"],
                "independent_bearish_lanes": [],
            },
        }
    }


def _case(index: int, *, matching: bool = True) -> dict:
    return {
        "episode_id": f"episode-{index:02d}",
        "click_at": f"2026-09-{index % 3 + 1:02d}T10:00:00+05:30",
        "available_at": f"2026-09-{index % 3 + 1:02d}T12:05:00+05:30",
        "regime": {
            "regime_labels": ["TREND", "EXPANDING"] if matching else ["RANGE"],
        },
        "evidence": {
            "independent_bullish_lanes": ["STRUCTURE", "PARTICIPATION"] if matching else [],
            "independent_bearish_lanes": [] if matching else ["GLOBAL"],
        },
        "action": "BUY_CE" if index % 2 == 0 else "NO_TRADE",
        "outcome": {
            "resolution_status": "RESOLVED",
            "underlying_return_pct": float(index),
            "max_up_atr": 1.0 + index / 10.0,
            "max_down_atr": 0.2,
            "geometry_outcome": "TARGET_FIRST" if index % 2 == 0 else "NOT_APPLICABLE",
            "diagnosis": "TRADE_EPISODE" if index % 2 == 0 else "NO_LARGE_CLEAN_MOVE_AFTER_ABSTENTION",
            "option_return_pct": 5.0 if index % 2 == 0 else None,
        },
    }


class _Cursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return []


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


class CrudeOilMiniProspectiveMemoryV1Tests(unittest.TestCase):
    def test_memory_collects_until_preregistered_case_count(self):
        cases = [_case(index) for index in range(MIN_READY_CASES - 1)]
        result = summarize_prospective_memory(cases, _current())

        self.assertEqual(result["status"], "COLLECTING")
        self.assertEqual(result["prior_resolved_cases"], MIN_READY_CASES - 1)
        self.assertFalse(result["promotion_eligible"])
        self.assertEqual(result["decision_effect"], "NONE")

    def test_ready_memory_remains_descriptive_and_shadow_only(self):
        cases = [_case(index, matching=index < 8) for index in range(MIN_READY_CASES)]
        result = summarize_prospective_memory(cases, _current())

        self.assertEqual(result["status"], "READY_FOR_DESCRIPTIVE_REVIEW")
        self.assertEqual(result["analogues_used"], MAX_ANALOGUES)
        self.assertEqual(result["retrieval_basis"], "SHARED_REGIME_LABELS_AND_EVIDENCE_LANES_ONLY")
        self.assertFalse(result["outcome_used_for_similarity_ranking"])
        self.assertEqual(result["current_mind_effect"], "NONE")
        self.assertEqual(result["integrated_v2_effect"], "NONE")
        self.assertEqual(result["option_expression_effect"], "NONE")
        self.assertFalse(result["promotion_eligible"])

    def test_outcome_mutation_cannot_change_analogue_selection(self):
        cases = [_case(index, matching=index < 10) for index in range(MIN_READY_CASES)]
        baseline = summarize_prospective_memory(cases, _current())

        mutated = copy.deepcopy(cases)
        for index, case in enumerate(mutated):
            case["outcome"] = {
                "resolution_status": "RESOLVED",
                "underlying_return_pct": -9999.0 + index,
                "max_up_atr": 99.0,
                "max_down_atr": 99.0,
                "geometry_outcome": "STOP_FIRST",
                "diagnosis": "MUTATED_OUTCOME",
                "option_return_pct": -100.0,
            }
        after = summarize_prospective_memory(mutated, _current())

        self.assertEqual(baseline["analogue_episode_ids"], after["analogue_episode_ids"])
        self.assertEqual(baseline["analogue_similarity_scores"], after["analogue_similarity_scores"])
        self.assertNotEqual(baseline["diagnosis_counts"], after["diagnosis_counts"])

    def test_database_reader_requires_strictly_prior_resolved_primary_outcomes(self):
        cursor = _Cursor()
        cutoff = datetime(2026, 9, 4, 19, 0, tzinfo=IST)
        with patch(
            "app.crude_oil_mini_prospective_memory_v1._connect",
            return_value=_Connection(cursor),
        ):
            rows = _load_cases_sync("postgresql://test", cutoff)

        self.assertEqual(rows, [])
        self.assertIn("o.horizon_minutes = %s", cursor.sql)
        self.assertIn("o.resolution_status = 'RESOLVED'", cursor.sql)
        self.assertIn("e.click_at < %s", cursor.sql)
        self.assertIn("o.available_at < %s", cursor.sql)
        self.assertNotIn("<= %s", cursor.sql)
        self.assertEqual(cursor.params[2], PRIMARY_OUTCOME_HORIZON_MINUTES)
        self.assertEqual(cursor.params[3], cutoff)
        self.assertEqual(cursor.params[4], cutoff)


if __name__ == "__main__":
    unittest.main()
