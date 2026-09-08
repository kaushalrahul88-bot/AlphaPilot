import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_historical_analogue import (
    BtcHistoricalAnaloguePolicy,
    derive_btc_historical_analogue_evidence,
)
from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow, HistoricalProvenance

UTC = timezone.utc
DECISION = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)


def _row(available_at: datetime, close: float, index: int) -> BtcSpotCandleArchiveRow:
    return BtcSpotCandleArchiveRow(
        open_at=available_at - timedelta(hours=1),
        close_at=available_at,
        available_at=available_at,
        open=close * 0.999,
        high=close * 1.002,
        low=close * 0.997,
        close=close,
        volume=100.0 + index,
        provenance=HistoricalProvenance(
            provider="COINDCX",
            source_id=f"analogue-{index}",
            availability_basis="BAR_COMPLETION_RECONSTRUCTION",
            point_in_time_proven=True,
            reconstructible_public_data=True,
        ),
    ).validated()


def _history() -> list[BtcSpotCandleArchiveRow]:
    start = DECISION - timedelta(hours=390)
    rows = []
    price = 70_000.0
    for i in range(391):
        # Deterministic alternating drift creates repeated but non-identical states.
        phase = (i // 12) % 4
        step = (35.0, 10.0, -30.0, -8.0)[phase]
        price += step
        rows.append(_row(start + timedelta(hours=i), price, i))
    return rows


class BtcHistoricalAnalogueTests(unittest.TestCase):
    def test_builds_context_only_memory_from_known_past_outcomes(self):
        evidence = derive_btc_historical_analogue_evidence(
            _history(),
            decision_at=DECISION,
            policy=BtcHistoricalAnaloguePolicy(
                lookback_hours=336,
                outcome_horizon_hours=1,
                min_candidate_spacing_hours=4,
                max_analogues=20,
                min_analogues=12,
            ),
        )
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.family, "BTC_HISTORICAL_ANALOGUE")
        self.assertEqual(evidence.causal_origin, "HISTORICAL_MEMORY")
        self.assertTrue(evidence.context_only)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.metadata["all_selected_outcomes_known_by_decision"])
        latest_known = datetime.fromisoformat(evidence.metadata["latest_selected_outcome_available_at"])
        self.assertLessEqual(latest_known, DECISION)
        self.assertFalse(evidence.metadata["direction_creator"])
        self.assertFalse(evidence.metadata["trade_generated"])

    def test_future_rows_cannot_change_current_memory(self):
        rows = _history()
        policy = BtcHistoricalAnaloguePolicy(lookback_hours=336, min_analogues=12)
        before = derive_btc_historical_analogue_evidence(rows, decision_at=DECISION, policy=policy)
        self.assertIsNotNone(before)

        future = list(rows)
        for i in range(1, 7):
            future.append(_row(DECISION + timedelta(hours=i), 120_000.0 + i * 5_000, 500 + i))
        after = derive_btc_historical_analogue_evidence(future, decision_at=DECISION, policy=policy)
        self.assertEqual(before, after)

    def test_insufficient_completed_history_returns_none(self):
        rows = _history()[-40:]
        evidence = derive_btc_historical_analogue_evidence(
            rows,
            decision_at=DECISION,
            policy=BtcHistoricalAnaloguePolicy(lookback_hours=48, min_analogues=12),
        )
        self.assertIsNone(evidence)


if __name__ == "__main__":
    unittest.main()
