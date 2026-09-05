import copy
import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_prospective_thesis_tape import (
    ImmutableProspectiveBtcThesisTape,
    ProspectiveBtcThesisTapePolicy,
    architecture_contract,
    freeze_prospective_btc_thesis,
    resolve_prospective_btc_thesis,
    verify_frozen_prospective_btc_thesis,
    verify_prospective_btc_thesis_resolution,
)
from app.crypto_btc_random_click_experience import BtcForwardPriceObservation
from app.crypto_market_intelligence import Evidence

UTC = timezone.utc
DECISION_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _policy(**overrides):
    values = {
        "trade_horizon": "intraday",
        "evaluation_horizon_hours": 1.0,
        "terminal_price_max_gap_seconds": 60,
        "neutral_band_pct": 0.25,
        "large_move_threshold_pct": 1.5,
    }
    values.update(overrides)
    return ProspectiveBtcThesisTapePolicy(**values).validated()


def _evidence(direction="BULLISH", *, second_origin=True, future=False):
    direction = direction.upper()
    at = DECISION_AT - timedelta(minutes=1)
    rows = [
        Evidence(
            family="BTC_SPOT_STRUCTURE",
            causal_origin="SPOT_PRICE_STRUCTURE",
            stance=direction,
            strength="MEDIUM",
            confidence=0.75,
            observed_at=at,
            reason="Prospective regression spot structure.",
            context_only=False,
            source="TEST_PROSPECTIVE_SPOT",
            metadata={"test_fixture": True},
        )
    ]
    if second_origin:
        rows.append(Evidence(
            family="DERIVATIVES_POSITIONING",
            causal_origin="LEVERAGED_POSITIONING",
            stance=direction,
            strength="MEDIUM",
            confidence=0.75,
            observed_at=at,
            reason="Prospective regression independent derivatives origin.",
            context_only=False,
            source="TEST_PROSPECTIVE_DERIVATIVES",
            metadata={"test_fixture": True},
        ))
    if future:
        rows.append(Evidence(
            family="CRYPTO_NEWS",
            causal_origin="EVENT_INFORMATION",
            stance=direction,
            strength="HIGH",
            confidence=0.9,
            observed_at=DECISION_AT + timedelta(seconds=1),
            reason="Future evidence must never enter the frozen decision.",
            context_only=False,
            source="TEST_FUTURE_NEWS",
            metadata={"test_fixture": True},
        ))
    return rows


def _freeze(direction="BULLISH", *, second_origin=True, click_id="prospective-001"):
    return freeze_prospective_btc_thesis(
        click_id=click_id,
        decision_at=DECISION_AT,
        btc_spot_price=100_000.0,
        evidence=_evidence(direction, second_origin=second_origin),
        policy=_policy(),
    )


def _prices(terminal_return_pct, *, terminal_at=None):
    terminal_at = terminal_at or (DECISION_AT + timedelta(hours=1))
    terminal = 100_000.0 * (1.0 + float(terminal_return_pct) / 100.0)
    return [
        BtcForwardPriceObservation(
            observed_at=DECISION_AT + timedelta(minutes=30),
            btc_price=100_500.0 if terminal_return_pct >= 0 else 99_500.0,
        ),
        BtcForwardPriceObservation(
            observed_at=terminal_at,
            btc_price=terminal,
        ),
    ]


class ProspectiveBtcThesisTapeTests(unittest.TestCase):
    def test_freezes_real_market_brain_direction_without_options_economics(self):
        frozen = _freeze("BULLISH")
        self.assertEqual(frozen["status"], "PROSPECTIVE_THESIS_FROZEN")
        self.assertEqual(frozen["decision"]["market_direction"], "BULLISH")
        self.assertIn("SPOT_PRICE_STRUCTURE", frozen["decision"]["counted_causal_origins"])
        self.assertIn("LEVERAGED_POSITIONING", frozen["decision"]["counted_causal_origins"])
        self.assertTrue(verify_frozen_prospective_btc_thesis(frozen))
        self.assertFalse(frozen["options_contract_data_used"])
        self.assertFalse(frozen["options_execution_metadata_used"])
        self.assertFalse(frozen["options_pnl_measured"])
        self.assertFalse(frozen["options_trade_generated"])
        self.assertFalse(frozen["futures_route_invoked"])
        self.assertFalse(frozen["futures_trade_generated"])
        self.assertFalse(frozen["live_execution"])
        self.assertEqual(frozen["capital_committed"], 0)

    def test_future_evidence_is_rejected_before_decision_freezes(self):
        with self.assertRaises(ValueError):
            freeze_prospective_btc_thesis(
                click_id="future-evidence",
                decision_at=DECISION_AT,
                btc_spot_price=100_000.0,
                evidence=_evidence("BULLISH", future=True),
                policy=_policy(),
            )

    def test_resolution_before_frozen_horizon_is_not_due(self):
        frozen = _freeze()
        result = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(minutes=59),
            forward_prices=[BtcForwardPriceObservation(
                observed_at=DECISION_AT + timedelta(minutes=30),
                btc_price=101_000.0,
            )],
        )
        self.assertEqual(result["status"], "THESIS_OUTCOME_NOT_DUE")
        self.assertIsNone(result["outcome"])
        self.assertFalse(result["performance_eligible"])
        self.assertFalse(result["decision_rewritten"])

    def test_bullish_and_bearish_hits_reuse_underlying_validator_scoring(self):
        bullish = _freeze("BULLISH", click_id="bull")
        bull_result = resolve_prospective_btc_thesis(
            frozen_record=bullish,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=_prices(2.0),
        )
        self.assertEqual(bull_result["status"], "THESIS_OUTCOME_RESOLVED")
        self.assertEqual(bull_result["outcome"]["classification"], "DIRECTIONAL_HIT")
        self.assertTrue(bull_result["outcome"]["directional_hit"])
        self.assertTrue(verify_prospective_btc_thesis_resolution(bull_result))

        bearish = _freeze("BEARISH", click_id="bear")
        bear_result = resolve_prospective_btc_thesis(
            frozen_record=bearish,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=_prices(-2.0),
        )
        self.assertEqual(bear_result["outcome"]["classification"], "DIRECTIONAL_HIT")
        self.assertEqual(bear_result["outcome"]["realized_direction"], "DOWN")

    def test_wrong_direction_is_directional_miss(self):
        frozen = _freeze("BULLISH")
        result = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=_prices(-2.0),
        )
        self.assertEqual(result["outcome"]["classification"], "DIRECTIONAL_MISS")
        self.assertFalse(result["outcome"]["directional_hit"])
        self.assertTrue(result["performance_eligible"])

    def test_unknown_large_move_is_forensic_abstention_not_scored_loss(self):
        frozen = _freeze("BULLISH", second_origin=False)
        self.assertEqual(frozen["decision"]["market_direction"], "UNKNOWN")
        result = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=_prices(3.0),
        )
        self.assertEqual(result["outcome"]["classification"], "ABSTENTION_RESOLVED")
        self.assertTrue(result["outcome"]["large_move_missed_during_abstention"])
        self.assertFalse(result["performance_eligible"])

    def test_flat_terminal_move_is_inconclusive(self):
        frozen = _freeze("BULLISH")
        result = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=_prices(0.10),
        )
        self.assertEqual(result["outcome"]["classification"], "DIRECTIONAL_INCONCLUSIVE")
        self.assertFalse(result["performance_eligible"])

    def test_missing_terminal_price_stays_unresolved_and_cannot_enter_tape(self):
        frozen = _freeze()
        result = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=[BtcForwardPriceObservation(
                observed_at=DECISION_AT + timedelta(minutes=30),
                btc_price=101_000.0,
            )],
        )
        self.assertEqual(result["status"], "THESIS_OUTCOME_UNRESOLVED")
        self.assertEqual(result["outcome"]["reason"], "TERMINAL_BTC_PRICE_TOO_FAR_FROM_HORIZON_END")
        tape = ImmutableProspectiveBtcThesisTape()
        tape.insert_frozen(frozen)
        with self.assertRaises(ValueError):
            tape.attach_resolution(result)

    def test_resolver_rejects_observation_from_after_resolution_time(self):
        frozen = _freeze()
        rows = _prices(2.0)
        rows.append(BtcForwardPriceObservation(
            observed_at=DECISION_AT + timedelta(hours=1, seconds=1),
            btc_price=102_100.0,
        ))
        with self.assertRaises(ValueError):
            resolve_prospective_btc_thesis(
                frozen_record=frozen,
                resolution_at=DECISION_AT + timedelta(hours=1),
                forward_prices=rows,
            )

    def test_future_outcome_changes_resolution_not_frozen_decision_fingerprint(self):
        frozen = _freeze()
        original_decision_fp = frozen["decision"]["decision_fingerprint"]
        hit = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=_prices(2.0),
        )
        miss = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=_prices(-2.0),
        )
        self.assertEqual(frozen["decision"]["decision_fingerprint"], original_decision_fp)
        self.assertEqual(hit["decision_fingerprint"], original_decision_fp)
        self.assertEqual(miss["decision_fingerprint"], original_decision_fp)
        self.assertNotEqual(hit["resolution_fingerprint"], miss["resolution_fingerprint"])

    def test_tampering_with_frozen_decision_breaks_verification(self):
        frozen = _freeze()
        tampered = copy.deepcopy(frozen)
        tampered["decision"]["market_direction"] = "BEARISH"
        self.assertFalse(verify_frozen_prospective_btc_thesis(tampered))
        with self.assertRaises(ValueError):
            resolve_prospective_btc_thesis(
                frozen_record=tampered,
                resolution_at=DECISION_AT + timedelta(hours=1),
                forward_prices=_prices(2.0),
            )

    def test_tape_is_idempotent_and_conflicting_outcome_cannot_overwrite(self):
        frozen = _freeze()
        tape = ImmutableProspectiveBtcThesisTape()
        self.assertEqual(tape.insert_frozen(frozen)["status"], "INSERTED_FROZEN_THESIS")
        self.assertEqual(tape.insert_frozen(frozen)["status"], "IDEMPOTENT_FROZEN_THESIS")

        hit = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=_prices(2.0),
        )
        self.assertEqual(tape.attach_resolution(hit)["status"], "ATTACHED_THESIS_RESOLUTION")
        self.assertEqual(tape.attach_resolution(hit)["status"], "IDEMPOTENT_THESIS_RESOLUTION")

        miss = resolve_prospective_btc_thesis(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            forward_prices=_prices(-2.0),
        )
        with self.assertRaises(ValueError):
            tape.attach_resolution(miss)
        self.assertEqual(tape.manifest()["decision_count"], 1)
        self.assertEqual(tape.manifest()["resolved_count"], 1)

    def test_pending_as_of_only_returns_due_unresolved_decisions(self):
        first = _freeze(click_id="first")
        second = freeze_prospective_btc_thesis(
            click_id="second",
            decision_at=DECISION_AT + timedelta(minutes=30),
            btc_spot_price=100_000.0,
            evidence=[
                Evidence(
                    family=row.family,
                    causal_origin=row.causal_origin,
                    stance=row.stance,
                    strength=row.strength,
                    confidence=row.confidence,
                    observed_at=DECISION_AT + timedelta(minutes=29),
                    reason=row.reason,
                    context_only=row.context_only,
                    source=row.source,
                    metadata=row.metadata,
                )
                for row in _evidence("BULLISH")
            ],
            policy=_policy(),
        )
        tape = ImmutableProspectiveBtcThesisTape()
        tape.insert_frozen(first)
        tape.insert_frozen(second)
        pending = tape.pending_as_of(DECISION_AT + timedelta(hours=1, minutes=1))
        self.assertEqual([row["decision"]["click_id"] for row in pending], ["first"])

    def test_architecture_is_proof_only_and_adds_no_provider_scheduler_or_execution(self):
        contract = architecture_contract()
        self.assertEqual(contract["purpose"], "PROVE_BTC_MARKET_BRAIN_BEFORE_OPTIONS_ECONOMICS")
        self.assertTrue(contract["uses_same_historical_thesis_scoring_primitives"])
        self.assertTrue(contract["decision_frozen_before_outcome"])
        self.assertTrue(contract["decision_and_resolution_stored_separately"])
        self.assertFalse(contract["unresolved_outcome_admitted_as_resolution"])
        self.assertFalse(contract["options_contract_data_required"])
        self.assertFalse(contract["options_execution_metadata_required"])
        self.assertFalse(contract["options_pnl_measured"])
        self.assertFalse(contract["futures_route_invoked"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)
        self.assertFalse(contract["automatic_provider_or_scheduler_added"])


if __name__ == "__main__":
    unittest.main()
