from __future__ import annotations

import unittest
from datetime import date

from app.fno_15m_backtest_api import _summary, architecture_contract as api_contract
from app.fno_15m_restart_safe_replay import (
    architecture_contract as replay_contract,
    dataset_key,
)


class Fno15mRestartSafeTests(unittest.TestCase):
    def test_dataset_key_is_stable_and_dataset_scoped(self):
        symbols = ["RELIANCE", "NIFTY"]
        dates = [date(2026, 8, 31), date(2026, 9, 4)]
        first = dataset_key(dates, symbols)
        second = dataset_key(dates, reversed(symbols))
        self.assertEqual(first, second)
        changed = dataset_key([date(2026, 8, 31), date(2026, 9, 7)], symbols)
        self.assertNotEqual(first, changed)

    def test_running_summary_is_durable_restart_safe(self):
        summary = _summary({
            "run_id": "run-1",
            "status": "RUNNING",
            "progress_json": {
                "stage": "HISTORICAL_CANDLE_CHECKPOINTS",
                "completed_histories": 17,
                "total_histories": 132,
            },
            "attempt_count": 2,
            "heartbeat_at": "2026-09-06T10:40:00+00:00",
            "deployment_commit": "abc123",
        })
        self.assertEqual(summary["status"], "RUNNING")
        self.assertTrue(summary["restart_safe"])
        self.assertEqual(summary["attempt_count"], 2)
        self.assertEqual(summary["progress"]["completed_histories"], 17)
        self.assertFalse(summary["live_execution"])
        self.assertEqual(summary["capital_committed"], 0)

    def test_completed_summary_surfaces_frozen_results(self):
        summary = _summary({
            "run_id": "run-2",
            "status": "COMPLETED",
            "attempt_count": 1,
            "result_json": {
                "mode": "FNO_15M_FULL_WINDOW_HISTORICAL_REPLAY_V1",
                "coverage": {"scheduled_clicks": 115},
                "results": {
                    "STRICT_V2": {"summary": {"scheduled_clicks": 115}},
                    "COVERAGE_30M": {"summary": {"scheduled_clicks": 115}},
                },
                "safety": {"live_execution": False, "capital_committed": 0},
            },
        })
        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(summary["coverage"]["scheduled_clicks"], 115)
        self.assertEqual(summary["strict_summary"]["scheduled_clicks"], 115)
        self.assertFalse(summary["safety"]["live_execution"])

    def test_contracts_preserve_strategy_and_execution_safety(self):
        api = api_contract()
        replay = replay_contract()
        self.assertTrue(api["durable_run_state"])
        self.assertTrue(api["durable_result"])
        self.assertTrue(api["automatic_resume_after_process_restart"])
        self.assertTrue(api["durable_reconstructible_candle_checkpoints"])
        self.assertFalse(api["strategy_policy_changed"])
        self.assertFalse(api["live_execution"])
        self.assertEqual(api["capital_committed"], 0)

        self.assertFalse(replay["strategy_logic_changed"])
        self.assertTrue(replay["point_in_time_option_chain_read_only"])
        self.assertTrue(replay["reconstructible_candle_cache_writes"])
        self.assertTrue(replay["resume_skips_completed_history_fetches"])
        self.assertFalse(replay["live_execution"])
        self.assertEqual(replay["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
