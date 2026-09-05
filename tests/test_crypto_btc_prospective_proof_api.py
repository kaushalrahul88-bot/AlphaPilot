from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.crypto_btc_prospective_proof_api import (
    architecture_contract as api_architecture_contract,
    freeze_one_prospective_btc_thesis,
)
from app.crypto_btc_prospective_proof_runtime import (
    BtcProspectiveProofRuntimeConfig,
    architecture_contract as runtime_architecture_contract,
)

UTC = timezone.utc
DECISION_AT = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)


class _ThesisStore:
    def __init__(self):
        self.records = []

    async def insert_frozen(self, record):
        self.records.append(record)
        return {
            "status": "INSERTED_FROZEN_THESIS",
            "click_id": record["decision"]["click_id"],
            "tape_fingerprint": record["tape_fingerprint"],
        }


class BtcProspectiveProofRuntimeTests(unittest.TestCase):
    def test_proof_store_is_disabled_by_default(self):
        config = BtcProspectiveProofRuntimeConfig.from_env({})
        self.assertFalse(config.postgres_enabled)
        self.assertEqual(config.evaluation_horizon_hours, 4.0)

    def test_enabled_proof_store_requires_database_url(self):
        with self.assertRaises(ValueError):
            BtcProspectiveProofRuntimeConfig.from_env({
                "ALPHAPILOT_CRYPTO_BTC_PROSPECTIVE_THESIS_POSTGRES_ENABLED": "true",
            })

    def test_runtime_contract_has_no_automatic_decisions_or_resolution(self):
        contract = runtime_architecture_contract()
        self.assertFalse(contract["automatic_decision_scheduler"])
        self.assertFalse(contract["automatic_outcome_resolution"])
        self.assertFalse(contract["caller_may_backdate_decision"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["live_execution"])


class BtcProspectiveProofApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_unresolved_input_is_never_persisted(self):
        store = _ThesisStore()

        async def unresolved(**kwargs):
            return {
                "status": "PROOF_INPUT_UNRESOLVED",
                "reason": "BTC_SPOT_STRUCTURE_UNAVAILABLE",
                "frozen_thesis": None,
                "trade_generated": False,
            }

        result = await freeze_one_prospective_btc_thesis(
            decision_at=DECISION_AT,
            click_id="btc-proof-unresolved",
            provider=object(),
            pit_store=object(),
            thesis_store=store,
            tape_policy=object(),
            freeze_func=unresolved,
        )
        self.assertFalse(result["decision_persisted"])
        self.assertEqual(store.records, [])
        self.assertFalse(result["automatic_outcome_resolution"])
        self.assertFalse(result["trade_generated"])

    async def test_valid_frozen_decision_is_persisted_once_without_execution(self):
        store = _ThesisStore()
        frozen = {
            "decision": {"click_id": "btc-proof-valid"},
            "tape_fingerprint": "tape-1",
        }

        async def resolved(**kwargs):
            self.assertEqual(kwargs["decision_at"], DECISION_AT)
            self.assertEqual(kwargs["click_id"], "btc-proof-valid")
            return {
                "status": "PROSPECTIVE_PROOF_DECISION_FROZEN",
                "frozen_thesis": frozen,
                "trade_generated": False,
            }

        result = await freeze_one_prospective_btc_thesis(
            decision_at=DECISION_AT,
            click_id="btc-proof-valid",
            provider=object(),
            pit_store=object(),
            thesis_store=store,
            tape_policy=object(),
            freeze_func=resolved,
        )
        self.assertTrue(result["decision_persisted"])
        self.assertEqual(store.records, [frozen])
        self.assertEqual(result["persistence_status"], "INSERTED_FROZEN_THESIS")
        self.assertFalse(result["automatic_outcome_resolution"])
        self.assertFalse(result["options_trade_generated"])
        self.assertFalse(result["futures_trade_generated"])
        self.assertFalse(result["live_execution"])
        self.assertEqual(result["capital_committed"], 0)

    def test_api_contract_is_explicit_server_time_only_research(self):
        contract = api_architecture_contract()
        self.assertEqual(contract["method"], "POST")
        self.assertTrue(contract["internal_collector_auth_required"])
        self.assertTrue(contract["server_time_only"])
        self.assertFalse(contract["caller_supplied_decision_at_allowed"])
        self.assertFalse(contract["historical_backfill_allowed"])
        self.assertTrue(contract["explicit_invocation_required"])
        self.assertFalse(contract["automatic_decision_scheduler"])
        self.assertFalse(contract["automatic_outcome_resolution"])
        self.assertFalse(contract["unresolved_input_persisted_as_decision"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
