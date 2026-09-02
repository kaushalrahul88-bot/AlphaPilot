from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from app import crude_oil_mini_current_mind as integrated
from app import crude_oil_mini_knowledge_board as knowledge_module


CLICK = "2026-08-18T14:20:00+05:30"


def _record(series: str, observed: str, available: str, value=None):
    return {
        "series": series,
        "observed_at": observed,
        "available_at": available,
        "source": "TEST",
        "value": {} if value is None else value,
        "quality": "OBSERVED",
    }


class CrudeOilMiniKnowledgeBoardTests(unittest.TestCase):
    def test_local_tape_alone_does_not_activate_external_domain_priors(self):
        board = knowledge_module.knowledge_board([
            _record("MCX_CRUDEOILM", "2026-08-18T14:15:00+05:30", "2026-08-18T14:20:00+05:30"),
        ], CLICK)
        self.assertEqual(board["knowledge_version"], "CRUDE_OIL_DOMAIN_KNOWLEDGE_V1")
        self.assertFalse(board["decision_vote"])
        self.assertFalse(board["decision_path_changed"])
        self.assertEqual(board["active_item_ids"], [])
        self.assertEqual(board["available_context_series"], ["MCX_CRUDEOILM"])

    def test_wti_requires_a_genuinely_visible_observation(self):
        future_wti = _record(
            "WTI_CRUDE",
            "2026-08-18T14:00:00+05:30",
            "2026-08-18T15:00:00+05:30",
        )
        board = knowledge_module.knowledge_board([future_wti], CLICK)
        self.assertNotIn("CL_MCX_WTI_BENCHMARK", board["active_item_ids"])

        visible_wti = dict(future_wti, available_at="2026-08-18T14:00:00+05:30")
        board = knowledge_module.knowledge_board([visible_wti], CLICK)
        self.assertIn("CL_MCX_WTI_BENCHMARK", board["active_item_ids"])

    def test_fx_translation_requires_both_wti_and_usdinr(self):
        wti = _record("WTI_CRUDE", "2026-08-18T14:00:00+05:30", "2026-08-18T14:00:00+05:30")
        fx = _record("USDINR", "2026-08-18T14:00:00+05:30", "2026-08-18T14:00:00+05:30")
        wti_only = knowledge_module.knowledge_board([wti], CLICK)
        both = knowledge_module.knowledge_board([wti, fx], CLICK)
        self.assertNotIn("CL_MCX_FX_TRANSLATION", wti_only["active_item_ids"])
        self.assertIn("CL_MCX_FX_TRANSLATION", both["active_item_ids"])

    def test_domain_knowledge_is_attached_after_decision_and_cannot_change_fingerprint(self):
        kwargs = {
            "click_timestamp": CLICK,
            "context_records": [
                _record("MCX_CRUDEOILM", "2026-08-18T14:15:00+05:30", CLICK, {"price": 8000.0}),
            ],
            "market_features": {
                "trend_structure": "RANGE",
                "volatility_regime": "NORMAL",
                "location": "IN_VALUE",
                "participation": "NORMAL",
                "opening_behavior": "BALANCED",
            },
            "evidence_items": [],
            "memory_cases": [],
        }
        with patch.object(integrated, "knowledge_board", return_value={"mode": "A", "decision_vote": False}):
            first = integrated.crude_oil_mini_current_mind_click(**kwargs)
        with patch.object(integrated, "knowledge_board", return_value={"mode": "B", "decision_vote": False, "extra": "changed"}):
            second = integrated.crude_oil_mini_current_mind_click(**kwargs)

        self.assertEqual(first["decision"], second["decision"])
        self.assertEqual(first["decision_fingerprint"], second["decision_fingerprint"])
        self.assertNotEqual(first["domain_knowledge"], second["domain_knowledge"])
        self.assertFalse(first["architecture"]["domain_knowledge_decision_vote"])

    def test_knowledge_board_has_no_copper_market_dependency(self):
        source = inspect.getsource(knowledge_module)
        self.assertNotIn("from .copper", source)
        self.assertNotIn("import copper", source)


if __name__ == "__main__":
    unittest.main()
