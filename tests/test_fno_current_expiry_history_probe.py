from __future__ import annotations

import unittest

from app.fno_current_expiry_history_probe import (
    SEARCH_DAYS,
    _contract_rows,
    _stamp,
    architecture_contract,
    representative_contracts,
)


class CurrentExpiryHistoryProbeTests(unittest.TestCase):
    def test_contract_parser_ignores_futures_and_selects_near_atm_options(self):
        rows = _contract_rows([
            "NSE-NIFTY-29Sep26-24900-CE",
            "NSE-NIFTY-29Sep26-24900-PE",
            "NSE-NIFTY-29Sep26-25000-CE",
            "NSE-NIFTY-29Sep26-25000-PE",
            "NSE-NIFTY-29Sep26-25100-CE",
            "NSE-NIFTY-29Sep26-25100-PE",
            "NSE-NIFTY-29Sep26-FUT",
        ])
        self.assertEqual(len(rows), 6)
        selected = representative_contracts(rows, 25020.0)
        self.assertEqual([row["type"] for row in selected], ["CE", "PE"])
        self.assertEqual({row["strike"] for row in selected}, {25000.0})

    def test_no_spot_uses_middle_strike(self):
        rows = _contract_rows([
            "NSE-ABC-29Sep26-90-CE",
            "NSE-ABC-29Sep26-90-PE",
            "NSE-ABC-29Sep26-100-CE",
            "NSE-ABC-29Sep26-100-PE",
            "NSE-ABC-29Sep26-110-CE",
            "NSE-ABC-29Sep26-110-PE",
        ])
        selected = representative_contracts(rows, None)
        self.assertEqual({row["strike"] for row in selected}, {100.0})

    def test_timestamp_parser_accepts_documented_and_epoch_forms(self):
        documented = _stamp("2026-08-31 09:15:00")
        epoch_seconds = _stamp(1788147900)
        epoch_millis = _stamp(1788147900000)
        self.assertIsNotNone(documented)
        self.assertIsNotNone(epoch_seconds)
        self.assertEqual(epoch_seconds, epoch_millis)

    def test_probe_stays_inside_documented_request_limits(self):
        self.assertLess(SEARCH_DAYS, 180)
        contract = architecture_contract()
        self.assertTrue(contract["daily_search_below_180_day_limit"])
        self.assertTrue(contract["five_minute_search_below_30_day_limit"])

    def test_safety_contract(self):
        contract = architecture_contract()
        self.assertTrue(contract["read_only"])
        self.assertTrue(contract["options_only"])
        self.assertFalse(contract["futures_sampled"])
        self.assertTrue(contract["coverage_must_be_proven_non_empty"])
        self.assertFalse(contract["point_in_time_chain_reconstructed"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
