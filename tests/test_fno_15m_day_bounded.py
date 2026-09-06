from __future__ import annotations

import unittest

from app.fno_15m_historical_replay_v1 import MAX_MEMORY_CASES
from app.fno_15m_restart_safe_replay_v2 import (
    _cap_memory,
    architecture_contract,
)


class Fno15mDayBoundedReplayTests(unittest.TestCase):
    def test_memory_cap_preserves_only_latest_visible_window(self):
        cases = [{"id": index} for index in range(MAX_MEMORY_CASES + 37)]
        _cap_memory(cases)
        self.assertEqual(len(cases), MAX_MEMORY_CASES)
        self.assertEqual(cases[0]["id"], 37)
        self.assertEqual(cases[-1]["id"], MAX_MEMORY_CASES + 36)

    def test_memory_cap_is_noop_within_limit(self):
        cases = [{"id": index} for index in range(12)]
        original = list(cases)
        _cap_memory(cases)
        self.assertEqual(cases, original)

    def test_contract_bounds_point_in_time_payloads_without_strategy_change(self):
        contract = architecture_contract()
        self.assertTrue(contract["frozen_strategy_logic"])
        self.assertTrue(contract["trading_dates_market_hours_only"])
        self.assertTrue(contract["point_in_time_option_chain_read_only"])
        self.assertEqual(contract["option_chain_payload_scope"], "ONE_TRADING_DAY")
        self.assertEqual(contract["memory_case_window"], MAX_MEMORY_CASES)
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)
        self.assertFalse(contract["futures_trade_generation"])


if __name__ == "__main__":
    unittest.main()
