from __future__ import annotations

import unittest

from app.copper_research_status import build_copper_research_status


class CopperResearchStatusTests(unittest.TestCase):
    def test_public_status_reports_data_without_unlocking_any_decision_path(self):
        board = {
            "status": "AVAILABLE",
            "groups": {
                "primary_market": {
                    "MCX_COPPER": {
                        "status": "AVAILABLE",
                        "visible_candles": 72,
                        "first_seen_immutable": True,
                        "provenance_id": "CANDLE_PROV",
                        "available_at": "2026-09-04T23:05:10+05:30",
                    }
                },
                "option_market": {
                    "MCX_COPPER_OPTION": {
                        "status": "AVAILABLE",
                        "contracts_visible": 6,
                        "first_seen_immutable": True,
                        "provenance_id": "OPTION_PROV",
                        "available_at": "2026-09-04T23:02:20+05:30",
                    }
                },
            },
        }
        direction = {
            "direction": "UNKNOWN",
            "shadow_only": True,
            "decision_effect": "NONE",
        }
        status = build_copper_research_status(board, direction)

        self.assertEqual(status["status"], "ACTIVE")
        self.assertEqual(status["trade_instrument"], "OPTIONS_ONLY")
        self.assertEqual(status["prospective_data"]["visible_5m_candles"], 72)
        self.assertEqual(status["prospective_data"]["option_contracts_visible"], 6)
        self.assertTrue(status["prospective_data"]["candle_first_seen_immutable"])
        self.assertTrue(status["prospective_data"]["option_first_seen_immutable"])
        self.assertFalse(status["prospective_data"]["historical_backfill_used"])
        self.assertTrue(status["sealed_forward_phase1"]["decision_rules_frozen"])
        self.assertFalse(status["sealed_forward_phase1"]["interim_score_exposed_by_status_endpoint"])
        self.assertEqual(status["pipeline"]["direction_v2"], "SHADOW_ONLY")
        self.assertEqual(status["pipeline"]["promotion"], "LOCKED")
        self.assertEqual(status["sealed_current_mind_effect"], "NONE")
        self.assertEqual(status["direction_v2_effect"], "NONE")
        self.assertFalse(status["production_rules_changed"])
        self.assertFalse(status["live_execution_enabled"])
        self.assertFalse(status["broker_order_placement_enabled"])
        self.assertEqual(status["capital_committed"], 0)
        self.assertFalse(status["promotion_eligible"])

    def test_zero_market_tape_stays_warming_up(self):
        board = {
            "status": "WARMING_UP",
            "groups": {
                "primary_market": {"MCX_COPPER": {"status": "UNAVAILABLE"}},
                "option_market": {"MCX_COPPER_OPTION": {"status": "AVAILABLE", "contracts_visible": 6}},
            },
        }
        status = build_copper_research_status(board, {"direction": "UNKNOWN"})
        self.assertEqual(status["status"], "WARMING_UP")
        self.assertEqual(status["pipeline"]["immutable_market_tape"], "WARMING_UP")
        self.assertEqual(status["pipeline"]["immutable_option_tape"], "ACTIVE")
        self.assertFalse(status["promotion_eligible"])


if __name__ == "__main__":
    unittest.main()
