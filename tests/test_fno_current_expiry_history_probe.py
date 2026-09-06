from __future__ import annotations

import unittest

from app.fno_current_expiry_history_probe import _contract_rows, representative_contracts, architecture_contract


class CurrentExpiryHistoryProbeTests(unittest.TestCase):
    def test_contract_parser_and_near_atm_selection(self):
        rows = _contract_rows([
            "NSE-NIFTY-29Sep26-24900-CE",
            "NSE-NIFTY-29Sep26-24900-PE",
            "NSE-NIFTY-29Sep26-25000-CE",
            "NSE-NIFTY-29Sep26-25000-PE",
            "NSE-NIFTY-29Sep26-25100-CE",
            "NSE-NIFTY-29Sep26-25100-PE",
            "NSE-NIFTY-29Sep26-FUT",
        ])
        selected = representative_contracts(rows, 25020.0)
        self.assertEqual([row["type"] for row in selected], ["CE", "PE", "FUT"])
        self.assertEqual(selected[0]["strike"], 25000.0)
        self.assertEqual(selected[1]["strike"], 25000.0)

    def test_no_spot_uses_middle_strike_without_future_data(self):
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

    def test_safety_contract(self):
        contract = architecture_contract()
        self.assertTrue(contract["read_only"])
        self.assertFalse(contract["point_in_time_chain_reconstructed"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
