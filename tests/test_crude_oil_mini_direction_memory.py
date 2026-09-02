from __future__ import annotations

import inspect
import unittest

from app import crude_oil_mini_direction_memory as memory


def _snapshot(seed: float = 0.0) -> dict:
    return {
        "return_15m_pct": 0.10 + seed,
        "return_60m_pct": 0.20 + seed,
        "ema20_gap_pct": 0.05 + seed,
        "ema50_gap_pct": 0.08 + seed,
        "atr_pct": 0.30,
        "relative_volume": 1.1,
        "time_adjusted_relative_volume": 1.2,
        "session_return_pct": 0.4 + seed,
        "session_range_position": 0.7,
        "session_vwap_gap_pct": 0.1,
        "opening_range_position": 0.8,
        "structure": "UPTREND",
        "opening_range_break": "ABOVE",
        "price_oi_state": "LONG_BUILDUP",
    }


def _case(index: int, *, positive: bool = True, available_at: str | None = None) -> dict:
    sign = 1.0 if positive else -1.0
    return memory.make_direction_case(
        snapshot=_snapshot(index * 0.0001),
        click_timestamp=f"2026-06-{(index % 20) + 1:02d}T10:00:00+05:30",
        available_at=available_at or f"2026-06-{(index % 20) + 1:02d}T12:00:00+05:30",
        future_returns_pct={
            "15": sign * 0.1,
            "30": sign * 0.2,
            "60": sign * 0.4,
            "120": sign * 0.6,
        },
    )


class CrudeOilMiniDirectionMemoryTests(unittest.TestCase):
    def test_case_contains_underlying_direction_not_trade_geometry(self):
        case = _case(1)
        self.assertTrue(case["geometry_independent"])
        self.assertIn("future_returns_pct", case)
        for forbidden in ("action", "entry_price", "stop_price", "target_price", "realized_r", "outcome"):
            self.assertNotIn(forbidden, case)

    def test_only_strictly_prior_resolved_cases_are_visible(self):
        cases = [_case(index) for index in range(25)]
        cases.append(_case(30, available_at="2026-09-03T14:00:00+05:30"))
        cases.append(_case(31, available_at="2026-09-03T15:00:00+05:30"))
        result = memory.query_direction_memory(
            cases,
            _snapshot(),
            "2026-09-03T14:00:00+05:30",
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["prior_resolved_cases"], 25)
        self.assertEqual(result["withheld_cases"], 2)
        self.assertEqual(result["stance"], "BULLISH")
        self.assertEqual(result["persistence"], "PERSISTS_TO_120M")

    def test_mixed_memory_does_not_force_direction(self):
        cases = [_case(index, positive=index % 2 == 0) for index in range(40)]
        result = memory.query_direction_memory(cases, _snapshot(), "2026-09-03T14:00:00+05:30")
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["stance"], "UNKNOWN")

    def test_insufficient_memory_abstains(self):
        result = memory.query_direction_memory(
            [_case(index) for index in range(10)],
            _snapshot(),
            "2026-09-03T14:00:00+05:30",
        )
        self.assertEqual(result["status"], "INSUFFICIENT_MEMORY")
        self.assertEqual(result["stance"], "UNKNOWN")

    def test_module_has_no_trade_geometry_dependency(self):
        source = inspect.getsource(memory)
        self.assertNotIn("TARGET_FIRST", source)
        self.assertNotIn("STOP_FIRST", source)
        self.assertNotIn("BUY_CE", source)
        self.assertNotIn("BUY_PE", source)
        contract = memory.architecture_contract()
        self.assertTrue(contract["geometry_independent"])
        self.assertFalse(contract["trade_outcome_labels_used"])
        self.assertFalse(contract["entry_stop_target_used"])


if __name__ == "__main__":
    unittest.main()
