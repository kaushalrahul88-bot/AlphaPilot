from __future__ import annotations

import unittest

from app.fno_data_readiness_audit_v2 import (
    CADENCE_SQL,
    DAILY_MARKET_COVERAGE_SQL,
    LEG_COMPLETENESS_SQL,
    SNAPSHOT_SUMMARY_SQL,
    TABLE_CAPABILITY_SQL,
    architecture_contract,
    assess_readiness,
)


class FnoDataReadinessAuditV2Tests(unittest.TestCase):
    def test_sql_is_select_only(self):
        for sql in (SNAPSHOT_SUMMARY_SQL, DAILY_MARKET_COVERAGE_SQL, LEG_COMPLETENESS_SQL, CADENCE_SQL, TABLE_CAPABILITY_SQL):
            lowered = sql.lower().lstrip()
            self.assertTrue(lowered.startswith(("select", "with")))
            for forbidden in ("insert ", "update ", "delete ", "alter ", "drop ", "truncate "):
                self.assertNotIn(forbidden, lowered)

    def test_current_observed_shape_is_not_live_ready(self):
        result = assess_readiness(
            {"snapshot_rows": 32203, "underlyings": 215},
            {
                "legs": 673826,
                "trading_symbol_present": 673826,
                "ltp_present": 673826,
                "oi_present": 673826,
                "volume_present": 673826,
                "iv_present": 673826,
                "delta_present": 673826,
                "bid_present": 0,
                "ask_present": 0,
            },
            {"median_gap_min": 27.94, "p90_gap_min": 35.90},
            market_hours_days=5,
            fno_decision_ledger_present=False,
            fno_outcome_ledger_present=False,
            historical_option_candle_probe_reliable=False,
        )
        self.assertEqual(result["capabilities"]["option_chain_point_in_time_replay"], "AVAILABLE_BUT_SHORT_WINDOW")
        self.assertEqual(result["capabilities"]["spread_slippage_replay"], "NOT_READY")
        self.assertIn("NO_FNO_PROSPECTIVE_OUTCOME_LEDGER", result["blocking_gaps"])
        self.assertFalse(result["ready_for_live_money"])

    def test_architecture_contract(self):
        contract = architecture_contract()
        self.assertTrue(contract["read_only"])
        self.assertTrue(contract["select_only_sql"])
        self.assertFalse(contract["database_writes"])
        self.assertFalse(contract["strategy_policy_changed"])
        self.assertFalse(contract["live_execution"])


if __name__ == "__main__":
    unittest.main()
