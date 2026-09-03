from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.crude_oil_mini_direction_brain_v2_integrated import (
    evaluate_integrated_direction_v2_shadow,
    integration_contract,
)
from app.crude_oil_mini_event_reaction_v3 import build_event_reaction_family
from app.crude_oil_mini_participation_v2 import build_participation_observation

IST = timezone(timedelta(hours=5, minutes=30))


def _global_feed(start_hour: int, *, bullish: bool = True) -> dict:
    rows = []
    for i in range(8):
        level = 100.0 + i if bullish else 108.0 - i
        start = datetime(2026, 8, 20, start_hour + i, tzinfo=IST)
        rows.append({
            "bar_start": start.isoformat(),
            "available_at": (start + timedelta(hours=1)).isoformat(),
            "open": level,
            "high": level + 1.0,
            "low": level - 1.0,
            "close": level + (0.4 if bullish else -0.4),
            "volume": 1000.0 + i * 10.0,
        })
    return {
        "status": "AVAILABLE",
        "source": "TEST",
        "bar_minutes": 60,
        "data": rows,
    }


def _context_probe(*, bullish: bool = True) -> dict:
    return {
        "feeds": {
            "WTI_CRUDE": _global_feed(9, bullish=bullish),
            "BRENT_CRUDE": _global_feed(9, bullish=bullish),
        }
    }


def _mini_candles(*, with_oi: bool = False):
    rows = []
    start = datetime(2026, 8, 20, 17, 0, tzinfo=IST)
    for i in range(6):
        level = 100.0 + i * 0.5
        oi = 1000.0 + i * 25.0 if with_oi else None
        rows.append([
            (start + timedelta(minutes=5 * i)).isoformat(),
            level,
            level + 0.5,
            level - 0.3,
            level + 0.2,
            100.0 + i * 20.0,
            oi,
        ])
    return rows


def _snapshot(*, directional: bool = True) -> dict:
    return {
        "structure": "UPTREND" if directional else "RANGE",
        "return_15m_pct": 0.3 if directional else 0.0,
        "return_60m_pct": 0.8 if directional else 0.0,
        "time_adjusted_relative_volume": 2.0,
        "session_vwap_gap_pct": 0.5,
    }


class CrudeOilMiniDirectionV2IntegratedTests(unittest.TestCase):
    def test_price_volume_without_oi_cannot_be_independent_participation(self):
        row = build_participation_observation(
            _mini_candles(with_oi=False),
            click_timestamp="2026-08-20T17:30:00+05:30",
            snapshot=_snapshot(),
            profile={"participation_confirming": 1.0},
        )
        self.assertEqual(row["state"], "PRICE_VOLUME_ONLY_DEPENDENT")
        self.assertEqual(row["independence_status"], "DEPENDENT_ON_LOCAL_PRICE")
        self.assertFalse(row["counts_for_direction"])

    def test_local_plus_richer_global_can_form_shadow_direction_without_fake_participation(self):
        result = evaluate_integrated_direction_v2_shadow(
            click_timestamp="2026-08-20T17:30:00+05:30",
            snapshot=_snapshot(),
            profile={"participation_confirming": 1.0},
            mini_candles=_mini_candles(with_oi=False),
            global_context_probe=_context_probe(bullish=True),
            context_records=[],
            event_records=[],
            direction_memory_cases=[],
        )
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["direction_confidence"], "MODERATE")
        self.assertEqual(
            result["supporting_families"],
            ["GLOBAL_CRUDE", "LOCAL_STRUCTURE"],
        )
        self.assertNotIn("PARTICIPATION", result["dependency_audit"]["counted_families"])

    def test_event_confirmed_by_global_crude_cannot_double_count_global_crude(self):
        event = {
            "series": "CRUDE_NEWS",
            "event_id": "event-1",
            "event_type": "SUPPLY_DISRUPTION",
            "observed_at": "2026-08-20T10:00:00+05:30",
            "available_at": "2026-08-20T10:00:00+05:30",
            "value": {
                "mechanism_stance": "BULLISH",
                "materiality_status": "MATERIAL",
                "novelty_status": "NEW",
                "reaction": {
                    "direction": "BULLISH",
                    "confirmed": True,
                    "confirmation_sources": ["WTI_CRUDE"],
                },
            },
        }
        result = evaluate_integrated_direction_v2_shadow(
            click_timestamp="2026-08-20T17:30:00+05:30",
            snapshot=_snapshot(directional=False),
            profile={"participation_confirming": 1.0},
            mini_candles=_mini_candles(with_oi=False),
            global_context_probe=_context_probe(bullish=True),
            context_records=[],
            event_records=[event],
            direction_memory_cases=[],
        )
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["direction_confidence"], "WEAK")
        self.assertEqual(result["dependency_audit"]["counted_families"], ["GLOBAL_CRUDE"])
        reasons = {row["reason"] for row in result["dependency_audit"]["suppressed"]}
        self.assertIn("DEPENDENT_ON_COUNTED_CAUSAL_ORIGIN", reasons)

    def test_reaction_without_declared_confirmation_source_fails_closed(self):
        event = {
            "series": "CRUDE_NEWS",
            "event_id": "event-2",
            "event_type": "SUPPLY_DISRUPTION",
            "observed_at": "2026-08-20T10:00:00+05:30",
            "available_at": "2026-08-20T10:00:00+05:30",
            "value": {
                "mechanism_stance": "BULLISH",
                "materiality_status": "MATERIAL",
                "novelty_status": "NEW",
                "reaction": {"direction": "BULLISH", "confirmed": True},
            },
        }
        family = build_event_reaction_family([event], "2026-08-20T17:30:00+05:30")
        self.assertEqual(family["state"], "REACTION_DEPENDENCY_UNAUDITABLE")
        self.assertFalse(family["counts_for_direction"])

    def test_integration_contract_has_no_forward_schedule_or_fixed_direction_horizon(self):
        contract = integration_contract()
        self.assertTrue(contract["research_only"])
        self.assertTrue(contract["shadow_only"])
        self.assertFalse(contract["prospective_schedule_defined_here"])
        self.assertFalse(contract["fixed_direction_horizon_defined_here"])
        self.assertFalse(contract["promotion_allowed"])
        self.assertTrue(contract["requires_separate_diagnostic_replay_approval"])


if __name__ == "__main__":
    unittest.main()
