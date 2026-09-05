from __future__ import annotations

import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.shared_commodity_brain_dashboard_api import (
    MODE,
    _dashboard_status_from_rows,
    _safe_parity_view,
)

IST = ZoneInfo("Asia/Kolkata")


class SharedCommodityBrainDashboardTests(unittest.TestCase):
    def test_safe_parity_view_exposes_research_thesis_only(self):
        parity = {
            "mode": "CRUDE_OIL_MINI_SHARED_BRAIN_PARITY_V1",
            "status": "EVALUATED",
            "legacy": {
                "direction": "BULLISH",
                "confidence": "STRONG",
                "thesis_state": "ALIGNED",
                "supporting_families": ["LOCAL_STRUCTURE", "GLOBAL_CRUDE"],
                "opposing_families": [],
                "secret": "must-not-leak",
            },
            "shared": {
                "direction": "BULLISH",
                "confidence": "MODERATE",
                "thesis_state": "ALIGNED",
                "supporting_families": ["LOCAL_STRUCTURE", "GLOBAL_CRUDE"],
                "opposing_families": [],
                "broker_order": {"id": "forbidden"},
            },
            "direction_agreement": True,
            "confidence_agreement": False,
            "full_thesis_agreement": False,
            "divergence_reason": "CONFIDENCE_OR_DEPENDENCY_AUDIT_DIFFERENCE",
            "memory_policy": {
                "legacy_memory_counted": False,
                "shared_memory_role": "EXPERIENCE_CONTEXT",
                "shared_memory_counts_as_independent_confirmation": False,
            },
            "pnl": 999,
        }
        safe = _safe_parity_view(parity)
        self.assertEqual(safe["legacy"]["direction"], "BULLISH")
        self.assertEqual(safe["shared"]["confidence"], "MODERATE")
        self.assertNotIn("secret", safe["legacy"])
        self.assertNotIn("broker_order", safe["shared"])
        self.assertNotIn("pnl", safe)

    def test_dashboard_status_hides_sealed_copper_and_outcomes(self):
        at = datetime(2026, 9, 7, 10, 0, tzinfo=IST)
        copper_rows = [
            (at, "BULLISH", "MODERATE", "ALIGNED", ["LOCAL_STRUCTURE", "OPTION_PARTICIPATION"], []),
        ]
        decision = {
            "shared_commodity_brain_parity_v1": {
                "mode": "CRUDE_OIL_MINI_SHARED_BRAIN_PARITY_V1",
                "status": "EVALUATED",
                "legacy": {"direction": "BULLISH", "confidence": "STRONG"},
                "shared": {"direction": "BULLISH", "confidence": "STRONG"},
                "direction_agreement": True,
                "confidence_agreement": True,
                "full_thesis_agreement": True,
                "divergence_reason": "NONE",
                "memory_policy": {},
            },
            "current_mind": {"action": "BUY_CE", "sealed": True},
            "execution": {"broker_order": "must-not-leak"},
        }
        crude_rows = [(at, json.dumps({"decision": decision}))]
        status = _dashboard_status_from_rows(copper_rows, crude_rows)

        self.assertEqual(status["mode"], MODE)
        self.assertTrue(status["research_only"])
        self.assertTrue(status["read_only"])
        self.assertEqual(status["copper"]["prospective_evaluations"], 1)
        self.assertFalse(status["copper"]["sealed_current_mind_phase1_visible"])
        self.assertEqual(status["crude_oil_mini"]["shared_parity_episodes"], 1)
        self.assertTrue(status["crude_oil_mini"]["latest_parity"]["full_thesis_agreement"])
        self.assertFalse(status["safety"]["copper_phase1_sealed_outputs_exposed"])
        self.assertFalse(status["safety"]["outcomes_or_pnl_exposed"])
        self.assertFalse(status["safety"]["broker_order_placement_enabled"])
        serialized = json.dumps(status, sort_keys=True).lower()
        self.assertNotIn("buy_ce", serialized)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn('"broker_order":', serialized)
        self.assertNotIn("current_mind", serialized.replace("sealed_current_mind_phase1_visible", "").replace("sealed_current_mind_effect", ""))

    def test_empty_streams_report_waiting_state(self):
        status = _dashboard_status_from_rows([], [])
        self.assertEqual(status["copper"]["status"], "WAITING_FOR_FIRST_PROSPECTIVE_SAMPLE")
        self.assertEqual(status["crude_oil_mini"]["status"], "WAITING_FOR_FIRST_SHARED_PROSPECTIVE_SAMPLE")
        self.assertIsNone(status["copper"]["latest"])
        self.assertIsNone(status["crude_oil_mini"]["latest_parity"])


if __name__ == "__main__":
    unittest.main()
