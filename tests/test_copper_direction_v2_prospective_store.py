from __future__ import annotations

import unittest

from app.copper_direction_brain_v2_shadow import evaluate_copper_direction_v2_shadow
from app.copper_direction_v2_prospective_store import (
    INSERT_FIRST_SEEN_SQL,
    MODEL_ID,
    build_prospective_record,
)


def _board(as_of="2026-09-04T23:00:00+05:30"):
    return {
        "as_of": as_of,
        "groups": {
            "primary_market": {
                "MCX_COPPER": {
                    "status": "AVAILABLE",
                    "perception_status": "READY",
                    "perception_snapshot": {
                        "structure": "UPTREND",
                        "return_15m_pct": 0.2,
                        "return_60m_pct": 0.5,
                        "session_vwap_gap_pct": 0.1,
                        "opening_range_break": "ABOVE",
                        "price_oi_state": "LONG_BUILDUP",
                    },
                }
            },
            "option_market": {
                "MCX_COPPER_OPTION": {
                    "status": "AVAILABLE",
                    "sample_bucket_at": "2026-09-04T22:55:00+05:30",
                    "put_call_oi_ratio": 1.4,
                    "ce_open_interest": 1000,
                    "pe_open_interest": 1400,
                    "first_seen_immutable": True,
                }
            },
            "global_copper": {
                "COMEX_HG": {"status": "UNAVAILABLE", "reason": "NO_TAPE"},
                "LME_COPPER": {"status": "UNAVAILABLE", "reason": "NO_TAPE"},
            },
            "china_macro": {"MACRO_RELEASE": {"status": "UNAVAILABLE"}},
            "news": {"COPPER_NEWS": {"status": "UNAVAILABLE"}},
            "currency": {
                "USDINR_INTRADAY": {"status": "UNAVAILABLE"},
                "SLOW_REFERENCE_FX": {"status": "UNAVAILABLE"},
            },
            "positioning": {"CFTC_COPPER": {"status": "UNAVAILABLE"}},
        },
    }


class CopperDirectionV2ProspectiveStoreTests(unittest.TestCase):
    def test_later_outcome_field_cannot_change_stored_prediction_payload(self):
        board = _board()
        evaluation = evaluate_copper_direction_v2_shadow(board)
        clean = build_prospective_record(
            board,
            evaluation,
            evaluated_at="2026-09-04T23:00:01+05:30",
        )
        contaminated = {
            **evaluation,
            "outcome": "TARGET",
            "future_return": 3.5,
            "pnl": 99999,
        }
        later = build_prospective_record(
            board,
            contaminated,
            evaluated_at="2026-09-04T23:00:01+05:30",
        )
        self.assertEqual(clean["evaluation_id"], later["evaluation_id"])
        self.assertEqual(clean["record_hash"], later["record_hash"])
        self.assertEqual(clean["evaluation_snapshot"], later["evaluation_snapshot"])
        self.assertNotIn("outcome", later["evaluation_snapshot"])
        self.assertNotIn("future_return", later["evaluation_snapshot"])
        self.assertNotIn("pnl", later["evaluation_snapshot"])

    def test_board_with_future_or_outcome_data_is_rejected(self):
        board = _board()
        board["outcome"] = "TARGET"
        evaluation = evaluate_copper_direction_v2_shadow(board)
        with self.assertRaisesRegex(ValueError, "forbidden"):
            build_prospective_record(
                board,
                evaluation,
                evaluated_at="2026-09-04T23:00:01+05:30",
            )

    def test_historical_as_of_cannot_enter_prospective_ledger(self):
        board = _board(as_of="2026-09-04T22:00:00+05:30")
        evaluation = evaluate_copper_direction_v2_shadow(board)
        with self.assertRaisesRegex(ValueError, "Historical as_of"):
            build_prospective_record(
                board,
                evaluation,
                evaluated_at="2026-09-04T23:00:00+05:30",
            )

    def test_same_board_as_of_has_one_deterministic_immutable_identity(self):
        board = _board()
        evaluation = evaluate_copper_direction_v2_shadow(board)
        first = build_prospective_record(
            board,
            evaluation,
            evaluated_at="2026-09-04T23:00:01+05:30",
        )
        second = build_prospective_record(
            board,
            evaluation,
            evaluated_at="2026-09-04T23:00:02+05:30",
        )
        self.assertEqual(first["evaluation_id"], second["evaluation_id"])
        self.assertEqual(first["model_id"], MODEL_ID)
        self.assertEqual(first["board_as_of"], second["board_as_of"])

    def test_first_seen_insert_never_updates_existing_evaluation(self):
        sql = INSERT_FIRST_SEEN_SQL.upper()
        self.assertIn("ON CONFLICT (MODEL_ID, BOARD_AS_OF) DO NOTHING", sql)
        self.assertNotIn("DO UPDATE", sql)

    def test_store_rejects_any_execution_effect(self):
        board = _board()
        evaluation = evaluate_copper_direction_v2_shadow(board)
        evaluation["live_execution_enabled"] = True
        with self.assertRaisesRegex(ValueError, "Execution-enabled"):
            build_prospective_record(
                board,
                evaluation,
                evaluated_at="2026-09-04T23:00:01+05:30",
            )


if __name__ == "__main__":
    unittest.main()
