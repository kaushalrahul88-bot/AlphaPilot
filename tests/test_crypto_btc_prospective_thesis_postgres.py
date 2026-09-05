import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_prospective_thesis_postgres import (
    DECISION_TABLE,
    INSERT_DECISION_SQL,
    INSERT_RESOLUTION_SQL,
    PENDING_AS_OF_SQL,
    RESOLUTION_TABLE,
    SCHEMA_SQL,
    PostgresProspectiveBtcThesisTapeStore,
    architecture_contract,
    postgres_thesis_decision_params,
    postgres_thesis_resolution_params,
)
from app.crypto_btc_prospective_thesis_tape import (
    ProspectiveBtcThesisTapePolicy,
    freeze_prospective_btc_thesis,
    resolve_prospective_btc_thesis,
)
from app.crypto_btc_random_click_experience import BtcForwardPriceObservation
from app.crypto_market_intelligence import Evidence

UTC = timezone.utc
DECISION_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _evidence():
    at = DECISION_AT - timedelta(minutes=1)
    return [
        Evidence(
            family="BTC_SPOT_STRUCTURE",
            causal_origin="SPOT_PRICE_STRUCTURE",
            stance="BULLISH",
            strength="MEDIUM",
            confidence=0.75,
            observed_at=at,
            reason="Postgres proof fixture spot.",
            context_only=False,
            source="TEST_SPOT",
            metadata={"fixture": True},
        ),
        Evidence(
            family="DERIVATIVES_POSITIONING",
            causal_origin="LEVERAGED_POSITIONING",
            stance="BULLISH",
            strength="MEDIUM",
            confidence=0.75,
            observed_at=at,
            reason="Postgres proof fixture derivatives.",
            context_only=False,
            source="TEST_DERIVATIVES",
            metadata={"fixture": True},
        ),
    ]


def _frozen():
    return freeze_prospective_btc_thesis(
        click_id="postgres-proof-1",
        decision_at=DECISION_AT,
        btc_spot_price=100_000.0,
        evidence=_evidence(),
        policy=ProspectiveBtcThesisTapePolicy(
            trade_horizon="intraday",
            evaluation_horizon_hours=1.0,
            terminal_price_max_gap_seconds=60,
            neutral_band_pct=0.25,
            large_move_threshold_pct=1.5,
        ),
    )


def _resolved():
    return resolve_prospective_btc_thesis(
        frozen_record=_frozen(),
        resolution_at=DECISION_AT + timedelta(hours=1),
        forward_prices=[
            BtcForwardPriceObservation(
                observed_at=DECISION_AT + timedelta(minutes=30),
                btc_price=100_500.0,
            ),
            BtcForwardPriceObservation(
                observed_at=DECISION_AT + timedelta(hours=1),
                btc_price=102_000.0,
            ),
        ],
    )


class ProspectiveBtcThesisPostgresTests(unittest.TestCase):
    def test_store_requires_explicit_database_url(self):
        with self.assertRaises(ValueError):
            PostgresProspectiveBtcThesisTapeStore("")

    def test_decision_params_preserve_frozen_identity_without_outcome(self):
        frozen = _frozen()
        params = postgres_thesis_decision_params(frozen)
        self.assertEqual(params["click_id"], "postgres-proof-1")
        self.assertEqual(params["market_direction"], "BULLISH")
        self.assertEqual(params["decision_fingerprint"], frozen["decision"]["decision_fingerprint"])
        self.assertEqual(params["tape_fingerprint"], frozen["tape_fingerprint"])
        self.assertEqual(params["decision_at"], DECISION_AT)
        self.assertEqual(params["outcome_due_at"], DECISION_AT + timedelta(hours=1))
        self.assertIn('"future_outcome_present_in_decision":false', params["payload"])
        self.assertNotIn('"terminal_btc_price"', params["payload"])

    def test_resolution_params_require_resolved_btc_only_outcome(self):
        resolved = _resolved()
        params = postgres_thesis_resolution_params(resolved)
        self.assertEqual(params["click_id"], "postgres-proof-1")
        self.assertEqual(params["classification"], "DIRECTIONAL_HIT")
        self.assertEqual(params["resolution_at"], DECISION_AT + timedelta(hours=1))
        self.assertEqual(params["decision_fingerprint"], resolved["decision_fingerprint"])
        self.assertEqual(params["tape_fingerprint"], resolved["tape_fingerprint"])
        self.assertEqual(params["resolution_fingerprint"], resolved["resolution_fingerprint"])
        self.assertIn('"options_pnl_measured":false', params["payload"])

    def test_not_due_or_unresolved_outcome_cannot_be_persisted_as_resolution(self):
        frozen = _frozen()
        not_due = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(minutes=30),
            forward_prices=[],
        )
        with self.assertRaises(ValueError):
            postgres_thesis_resolution_params(not_due)

        unresolved = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=[BtcForwardPriceObservation(
                observed_at=DECISION_AT + timedelta(minutes=30),
                btc_price=101_000.0,
            )],
        )
        self.assertEqual(unresolved["status"], "THESIS_OUTCOME_UNRESOLVED")
        with self.assertRaises(ValueError):
            postgres_thesis_resolution_params(unresolved)

    def test_schema_uses_separate_insert_only_decision_and_resolution_tables(self):
        schema = " ".join(SCHEMA_SQL.upper().split())
        decision_insert = " ".join(INSERT_DECISION_SQL.upper().split())
        resolution_insert = " ".join(INSERT_RESOLUTION_SQL.upper().split())
        self.assertIn(DECISION_TABLE.upper(), schema)
        self.assertIn(RESOLUTION_TABLE.upper(), schema)
        self.assertIn("REFERENCES", schema)
        self.assertIn("CHECK (OUTCOME_DUE_AT > DECISION_AT)", schema)
        self.assertIn("CHECK (RESOLUTION_AT >= OUTCOME_DUE_AT)", schema)
        self.assertIn("ON CONFLICT", decision_insert)
        self.assertIn("DO NOTHING", decision_insert)
        self.assertNotIn("DO UPDATE", decision_insert)
        self.assertIn("ON CONFLICT", resolution_insert)
        self.assertIn("DO NOTHING", resolution_insert)
        self.assertNotIn("DO UPDATE", resolution_insert)
        self.assertNotIn("UPDATE ", schema)
        self.assertNotIn("DELETE ", schema)

    def test_pending_query_requires_due_decision_and_no_resolution(self):
        normalized = " ".join(PENDING_AS_OF_SQL.upper().split())
        self.assertIn("LEFT JOIN", normalized)
        self.assertIn("R.CLICK_ID IS NULL", normalized)
        self.assertIn("D.OUTCOME_DUE_AT <= %S", normalized)

    def test_decision_payload_with_execution_state_is_rejected(self):
        frozen = dict(_frozen())
        frozen["live_execution"] = True
        with self.assertRaises(ValueError):
            postgres_thesis_decision_params(frozen)

    def test_architecture_is_proof_persistence_only(self):
        contract = architecture_contract()
        self.assertFalse(contract["backend_automatically_selected"])
        self.assertFalse(contract["schema_initialization_starts_collection"])
        self.assertFalse(contract["schema_initialization_starts_execution"])
        self.assertTrue(contract["decision_and_resolution_tables_separate"])
        self.assertTrue(contract["insert_only"])
        self.assertFalse(contract["update_existing_decision_allowed"])
        self.assertFalse(contract["update_existing_resolution_allowed"])
        self.assertFalse(contract["delete_path_exposed"])
        self.assertFalse(contract["unresolved_resolution_persisted"])
        self.assertFalse(contract["market_data_pit_archive_used_for_outcomes"])
        self.assertFalse(contract["options_experience_store_used_for_underlying_only_outcome"])
        self.assertFalse(contract["options_pnl_measured"])
        self.assertFalse(contract["futures_execution_enabled"])
        self.assertFalse(contract["live_execution"])


if __name__ == "__main__":
    unittest.main()
