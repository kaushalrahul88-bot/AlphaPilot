from __future__ import annotations

import unittest

from app.crypto_btc_prospective_thesis_immutability import (
    DECISION_ROW_TRIGGER,
    DECISION_TRUNCATE_TRIGGER,
    FUNCTION_NAME,
    IMMUTABILITY_SQL,
    RESOLUTION_ROW_TRIGGER,
    RESOLUTION_TRUNCATE_TRIGGER,
    architecture_contract,
)
from app.crypto_btc_prospective_thesis_postgres import DECISION_TABLE, RESOLUTION_TABLE


class BtcProspectiveThesisImmutabilityTests(unittest.TestCase):
    def test_sql_blocks_update_delete_and_truncate_on_both_tables(self):
        sql = " ".join(IMMUTABILITY_SQL.upper().split())
        self.assertIn(f"CREATE OR REPLACE FUNCTION {FUNCTION_NAME.upper()}()", sql)
        self.assertIn(f"BEFORE UPDATE OR DELETE ON {DECISION_TABLE.upper()}", sql)
        self.assertIn(f"BEFORE TRUNCATE ON {DECISION_TABLE.upper()}", sql)
        self.assertIn(f"BEFORE UPDATE OR DELETE ON {RESOLUTION_TABLE.upper()}", sql)
        self.assertIn(f"BEFORE TRUNCATE ON {RESOLUTION_TABLE.upper()}", sql)
        for trigger in (
            DECISION_ROW_TRIGGER,
            DECISION_TRUNCATE_TRIGGER,
            RESOLUTION_ROW_TRIGGER,
            RESOLUTION_TRUNCATE_TRIGGER,
        ):
            self.assertIn(f"CREATE TRIGGER {trigger.upper()}", sql)
        self.assertIn("RAISE EXCEPTION", sql)

    def test_contract_is_database_enforced_without_runtime_side_effects(self):
        contract = architecture_contract()
        self.assertTrue(contract["database_enforced"])
        self.assertFalse(contract["decision_update_allowed"])
        self.assertFalse(contract["decision_delete_allowed"])
        self.assertFalse(contract["decision_truncate_allowed"])
        self.assertFalse(contract["resolution_update_allowed"])
        self.assertFalse(contract["resolution_delete_allowed"])
        self.assertFalse(contract["resolution_truncate_allowed"])
        self.assertFalse(contract["insert_path_changed"])
        self.assertFalse(contract["schema_hardening_starts_collection"])
        self.assertFalse(contract["schema_hardening_freezes_decision"])
        self.assertFalse(contract["schema_hardening_resolves_outcome"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["live_execution"])


if __name__ == "__main__":
    unittest.main()
