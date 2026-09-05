import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.crypto_btc_historical_data_adapter import (
    BtcHistoricalArchive,
    BtcHistoricalEvidenceRow,
    BtcSpotCandleArchiveRow,
    HistoricalProvenance,
)
from app.crypto_btc_random_click_experience import BtcRandomClickPolicy
from app.crypto_btc_underlying_thesis_validation import (
    BtcUnderlyingThesisValidationPolicy,
    architecture_contract,
    run_btc_underlying_thesis_validation,
    summarize_underlying_thesis_validation,
)
from app.crypto_market_intelligence import Evidence

UTC = timezone.utc
DECISION_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _candle_provenance(source_id: str) -> HistoricalProvenance:
    return HistoricalProvenance(
        provider="COINDCX",
        source_id=source_id,
        availability_basis="BAR_COMPLETION_RECONSTRUCTION",
        point_in_time_proven=True,
        reconstructible_public_data=True,
    )


def _evidence_provenance(source_id: str) -> HistoricalProvenance:
    return HistoricalProvenance(
        provider="TEST_PIT_PROVIDER",
        source_id=source_id,
        availability_basis="FIRST_SEEN_CAPTURE",
        point_in_time_proven=True,
        immutable_archive=True,
    )


def _history_candles(direction: str) -> list[BtcSpotCandleArchiveRow]:
    direction = direction.upper()
    if direction not in {"BULLISH", "BEARISH"}:
        raise ValueError("direction must be BULLISH or BEARISH")
    start_available = DECISION_AT - timedelta(hours=30)
    rows = []
    for i in range(31):
        available_at = start_available + timedelta(hours=i)
        if direction == "BULLISH":
            close = 97_000.0 + i * 100.0
            open_price = close - 40.0
            high = close + 10.0
            low = close - 90.0
        else:
            close = 103_000.0 - i * 100.0
            open_price = close + 40.0
            high = close + 90.0
            low = close - 10.0
        volume = 250.0 if available_at == DECISION_AT else 100.0
        rows.append(BtcSpotCandleArchiveRow(
            open_at=available_at - timedelta(hours=1),
            close_at=available_at,
            available_at=available_at,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            provenance=_candle_provenance(f"hist-{direction}-{i}"),
        ).validated())
    return rows


def _future_candle(*, terminal_return_pct: float, observed_at: datetime) -> BtcSpotCandleArchiveRow:
    entry = 100_000.0
    close = entry * (1.0 + float(terminal_return_pct) / 100.0)
    high = max(entry, close) + 25.0
    low = min(entry, close) - 25.0
    return BtcSpotCandleArchiveRow(
        open_at=observed_at - timedelta(minutes=1),
        close_at=observed_at,
        available_at=observed_at,
        open=entry,
        high=high,
        low=low,
        close=close,
        volume=150.0,
        provenance=_candle_provenance(f"future-{observed_at.isoformat()}-{terminal_return_pct}"),
    ).validated()


def _directional_evidence(direction: str, *, available_at: datetime, source_id: str = "second-origin") -> BtcHistoricalEvidenceRow:
    direction = direction.upper()
    return BtcHistoricalEvidenceRow(
        evidence=Evidence(
            family="DERIVATIVES_POSITIONING",
            causal_origin="LEVERAGED_POSITIONING",
            stance=direction,
            strength="MEDIUM",
            confidence=0.75,
            observed_at=available_at,
            reason="Synthetic point-in-time independent confirmation for regression testing.",
            context_only=False,
            source="TEST_PIT_DERIVATIVES",
            metadata={"synthetic_fixture": True},
        ),
        available_at=available_at,
        event_at=available_at,
        provenance=_evidence_provenance(source_id),
    ).validated()


def _archive(
    *,
    history_direction: str = "BULLISH",
    terminal_return_pct: float = 2.0,
    terminal_at: datetime | None = None,
    second_origin_direction: str | None = "BULLISH",
    future_evidence_direction: str | None = None,
) -> BtcHistoricalArchive:
    terminal_at = terminal_at or (DECISION_AT + timedelta(hours=1))
    candles = _history_candles(history_direction)
    candles.append(_future_candle(terminal_return_pct=terminal_return_pct, observed_at=terminal_at))
    evidence_rows = []
    if second_origin_direction is not None:
        evidence_rows.append(_directional_evidence(
            second_origin_direction,
            available_at=DECISION_AT - timedelta(minutes=5),
        ))
    if future_evidence_direction is not None:
        evidence_rows.append(_directional_evidence(
            future_evidence_direction,
            available_at=DECISION_AT + timedelta(minutes=10),
            source_id="future-origin",
        ))
    return BtcHistoricalArchive(
        spot_candles=tuple(candles),
        evidence_rows=tuple(evidence_rows),
        option_contract_rows=(),
        option_quote_rows=(),
        execution_rows=(),
    ).validated()


def _policy(*, seed: int = 17, neutral_band_pct: float = 0.25, terminal_gap_seconds: int = 60) -> BtcUnderlyingThesisValidationPolicy:
    return BtcUnderlyingThesisValidationPolicy(
        click_policy=BtcRandomClickPolicy(
            start_at=DECISION_AT,
            end_at=DECISION_AT + timedelta(seconds=1),
            click_count=1,
            seed=seed,
        ),
        trade_horizon="intraday",
        max_spot_age_seconds=60,
        evaluation_horizon_hours=1.0,
        terminal_price_max_gap_seconds=terminal_gap_seconds,
        neutral_band_pct=neutral_band_pct,
        large_move_threshold_pct=1.5,
    ).validated()


class BtcUnderlyingThesisValidationTests(unittest.TestCase):
    def test_bullish_thesis_hit_uses_real_market_brain_and_no_options_data(self):
        result = run_btc_underlying_thesis_validation(
            archive=_archive(history_direction="BULLISH", terminal_return_pct=2.0, second_origin_direction="BULLISH"),
            policy=_policy(),
        )
        row = result["click_results"][0]
        self.assertEqual(row["decision"]["market_direction"], "BULLISH")
        self.assertIn("SPOT_PRICE_STRUCTURE", row["decision"]["counted_causal_origins"])
        self.assertIn("LEVERAGED_POSITIONING", row["decision"]["counted_causal_origins"])
        self.assertEqual(row["outcome"]["classification"], "DIRECTIONAL_HIT")
        self.assertTrue(row["outcome"]["directional_hit"])
        self.assertEqual(result["summary"]["directional_accuracy"], 1.0)
        self.assertFalse(result["options_contract_data_required"])
        self.assertFalse(result["options_execution_metadata_required"])
        self.assertFalse(result["options_pnl_measured"])

    def test_bearish_thesis_hit(self):
        result = run_btc_underlying_thesis_validation(
            archive=_archive(history_direction="BEARISH", terminal_return_pct=-2.0, second_origin_direction="BEARISH"),
            policy=_policy(),
        )
        row = result["click_results"][0]
        self.assertEqual(row["decision"]["market_direction"], "BEARISH")
        self.assertEqual(row["outcome"]["realized_direction"], "DOWN")
        self.assertEqual(row["outcome"]["classification"], "DIRECTIONAL_HIT")

    def test_wrong_direction_is_a_miss(self):
        result = run_btc_underlying_thesis_validation(
            archive=_archive(history_direction="BULLISH", terminal_return_pct=-2.0, second_origin_direction="BULLISH"),
            policy=_policy(),
        )
        row = result["click_results"][0]
        self.assertEqual(row["decision"]["market_direction"], "BULLISH")
        self.assertEqual(row["outcome"]["classification"], "DIRECTIONAL_MISS")
        self.assertFalse(row["outcome"]["directional_hit"])
        self.assertEqual(result["summary"]["directional_accuracy"], 0.0)

    def test_unknown_abstention_with_large_move_is_recorded_but_not_scored_as_miss(self):
        result = run_btc_underlying_thesis_validation(
            archive=_archive(
                history_direction="BULLISH",
                terminal_return_pct=3.0,
                second_origin_direction=None,
            ),
            policy=_policy(),
        )
        row = result["click_results"][0]
        self.assertEqual(row["decision"]["market_direction"], "UNKNOWN")
        self.assertEqual(row["outcome"]["classification"], "ABSTENTION_RESOLVED")
        self.assertTrue(row["outcome"]["large_move_missed_during_abstention"])
        self.assertFalse(row["outcome"]["performance_eligible"])
        self.assertEqual(result["summary"]["directional_accuracy_denominator"], 0)
        self.assertEqual(result["summary"]["abstention_large_move_count"], 1)

    def test_flat_terminal_outcome_is_inconclusive_and_excluded(self):
        result = run_btc_underlying_thesis_validation(
            archive=_archive(history_direction="BULLISH", terminal_return_pct=0.10, second_origin_direction="BULLISH"),
            policy=_policy(neutral_band_pct=0.25),
        )
        row = result["click_results"][0]
        self.assertEqual(row["outcome"]["realized_direction"], "FLAT")
        self.assertEqual(row["outcome"]["classification"], "DIRECTIONAL_INCONCLUSIVE")
        self.assertFalse(row["outcome"]["performance_eligible"])
        self.assertEqual(result["summary"]["directional_accuracy_denominator"], 0)

    def test_terminal_price_too_far_from_horizon_is_unresolved_and_excluded(self):
        result = run_btc_underlying_thesis_validation(
            archive=_archive(
                history_direction="BULLISH",
                terminal_return_pct=2.0,
                terminal_at=DECISION_AT + timedelta(minutes=30),
                second_origin_direction="BULLISH",
            ),
            policy=_policy(terminal_gap_seconds=60),
        )
        row = result["click_results"][0]
        self.assertEqual(row["outcome"]["status"], "OUTCOME_UNRESOLVED")
        self.assertEqual(row["outcome"]["reason"], "TERMINAL_BTC_PRICE_TOO_FAR_FROM_HORIZON_END")
        self.assertFalse(row["performance_eligible"])
        self.assertEqual(result["summary"]["outcome_unresolved_count"], 1)
        self.assertEqual(result["summary"]["directional_accuracy_denominator"], 0)

    def test_future_evidence_cannot_change_earlier_click_direction_or_fingerprint(self):
        baseline = run_btc_underlying_thesis_validation(
            archive=_archive(history_direction="BULLISH", terminal_return_pct=2.0, second_origin_direction=None),
            policy=_policy(),
        )
        with_future = run_btc_underlying_thesis_validation(
            archive=_archive(
                history_direction="BULLISH",
                terminal_return_pct=2.0,
                second_origin_direction=None,
                future_evidence_direction="BULLISH",
            ),
            policy=_policy(),
        )
        baseline_decision = baseline["click_results"][0]["decision"]
        future_decision = with_future["click_results"][0]["decision"]
        self.assertEqual(baseline_decision["market_direction"], "UNKNOWN")
        self.assertEqual(future_decision["market_direction"], "UNKNOWN")
        self.assertEqual(baseline_decision["decision_fingerprint"], future_decision["decision_fingerprint"])
        self.assertLessEqual(
            datetime.fromisoformat(future_decision["latest_evidence_at"]),
            DECISION_AT,
        )

    def test_same_seed_reproduces_click_schedule_and_decision_fingerprint(self):
        archive = _archive(history_direction="BULLISH", terminal_return_pct=2.0, second_origin_direction="BULLISH")
        first = run_btc_underlying_thesis_validation(archive=archive, policy=_policy(seed=91))
        second = run_btc_underlying_thesis_validation(archive=archive, policy=_policy(seed=91))
        self.assertEqual(first["click_schedule"], second["click_schedule"])
        self.assertEqual(
            first["click_results"][0]["decision"]["decision_fingerprint"],
            second["click_results"][0]["decision"]["decision_fingerprint"],
        )

    def test_changing_only_future_outcome_does_not_change_decision_fingerprint(self):
        up = run_btc_underlying_thesis_validation(
            archive=_archive(history_direction="BULLISH", terminal_return_pct=2.0, second_origin_direction="BULLISH"),
            policy=_policy(),
        )
        down = run_btc_underlying_thesis_validation(
            archive=_archive(history_direction="BULLISH", terminal_return_pct=-2.0, second_origin_direction="BULLISH"),
            policy=_policy(),
        )
        up_row = up["click_results"][0]
        down_row = down["click_results"][0]
        self.assertEqual(up_row["decision"]["decision_fingerprint"], down_row["decision"]["decision_fingerprint"])
        self.assertEqual(up_row["outcome"]["classification"], "DIRECTIONAL_HIT")
        self.assertEqual(down_row["outcome"]["classification"], "DIRECTIONAL_MISS")

    def test_summary_denominator_uses_hits_and_misses_only(self):
        rows = [
            {"status": "CLICK_VALIDATED", "decision": {"market_direction": "BULLISH"}, "outcome": {"status": "OUTCOME_RESOLVED", "classification": "DIRECTIONAL_HIT"}},
            {"status": "CLICK_VALIDATED", "decision": {"market_direction": "BEARISH"}, "outcome": {"status": "OUTCOME_RESOLVED", "classification": "DIRECTIONAL_MISS"}},
            {"status": "CLICK_VALIDATED", "decision": {"market_direction": "BULLISH"}, "outcome": {"status": "OUTCOME_RESOLVED", "classification": "DIRECTIONAL_INCONCLUSIVE"}},
            {"status": "CLICK_VALIDATED", "decision": {"market_direction": "UNKNOWN"}, "outcome": {"status": "OUTCOME_RESOLVED", "classification": "ABSTENTION_RESOLVED", "large_move_missed_during_abstention": True}},
            {"status": "CLICK_VALIDATED", "decision": {"market_direction": "BULLISH"}, "outcome": {"status": "OUTCOME_UNRESOLVED"}},
            {"status": "CLICK_INPUT_UNRESOLVED", "decision": None, "outcome": None},
        ]
        summary = summarize_underlying_thesis_validation(rows)
        self.assertEqual(summary["directional_hit_count"], 1)
        self.assertEqual(summary["directional_miss_count"], 1)
        self.assertEqual(summary["directional_inconclusive_count"], 1)
        self.assertEqual(summary["abstention_count"], 1)
        self.assertEqual(summary["outcome_unresolved_count"], 1)
        self.assertEqual(summary["directional_accuracy_denominator"], 2)
        self.assertEqual(summary["directional_accuracy"], 0.5)
        self.assertEqual(summary["abstention_large_move_count"], 1)

    def test_architecture_contract_is_underlying_only_and_never_invokes_futures(self):
        contract = architecture_contract()
        self.assertTrue(contract["decision_evidence_point_in_time_only"])
        self.assertTrue(contract["decision_frozen_before_outcome"])
        self.assertTrue(contract["terminal_return_is_primary_directional_outcome"])
        self.assertTrue(contract["excursions_are_diagnostics_only"])
        self.assertFalse(contract["options_contract_data_required"])
        self.assertFalse(contract["options_execution_metadata_required"])
        self.assertFalse(contract["options_pnl_measured"])
        self.assertFalse(contract["historical_option_quotes_may_be_fabricated"])
        self.assertFalse(contract["futures_route_invoked"])
        self.assertFalse(contract["futures_fallback_allowed"])
        self.assertFalse(contract["broker_execution_enabled"])
        self.assertFalse(contract["live_execution"])


if __name__ == "__main__":
    unittest.main()
