import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow, HistoricalProvenance
from app.crypto_btc_prospective_proof_bridge import (
    ProspectiveBtcProofBridgePolicy,
    freeze_prospective_btc_thesis_from_existing_sources,
)
from app.crypto_btc_prospective_thesis_tape import ProspectiveBtcThesisTapePolicy

UTC = timezone.utc
DECISION = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)


def _row(at: datetime, close: float, i: int, minutes: int = 60) -> BtcSpotCandleArchiveRow:
    return BtcSpotCandleArchiveRow(
        open_at=at - timedelta(minutes=minutes),
        close_at=at,
        available_at=at,
        open=close - 20.0,
        high=close + 35.0,
        low=close - 45.0,
        close=close,
        volume=100.0 + (i % 24) * 3.0,
        provenance=HistoricalProvenance(
            provider="COINDCX",
            source_id=f"memory-bridge-{i}-{minutes}",
            availability_basis="BAR_COMPLETION_RECONSTRUCTION",
            point_in_time_proven=True,
            reconstructible_public_data=True,
        ),
    ).validated()


def _hourly_history() -> list[BtcSpotCandleArchiveRow]:
    start = DECISION - timedelta(hours=194)
    rows = []
    price = 78_000.0
    for i in range(195):
        phase = (i // 10) % 4
        price += (28.0, 8.0, -25.0, -6.0)[phase]
        rows.append(_row(start + timedelta(hours=i), price, i))
    return rows


class _Provider:
    def __init__(self):
        self.hourly = _hourly_history()

    def fetch_spot_candles(self, *, interval, start_at=None, end_at=None, limit=1000):
        if interval == "1h":
            return list(self.hourly)[-limit:]
        return [_row(DECISION, float(self.hourly[-1].close), 999, minutes=1)]


class _Store:
    async def visible_as_of(self, as_of):
        return []


def _tape_policy():
    return ProspectiveBtcThesisTapePolicy(
        trade_horizon="intraday",
        evaluation_horizon_hours=1.0,
        terminal_price_max_gap_seconds=60,
        neutral_band_pct=0.25,
        large_move_threshold_pct=1.5,
    ).validated()


class ProspectiveHistoricalMemoryBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_past_analogues_populate_historical_memory_lane(self):
        result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="memory-bridge",
            decision_at=DECISION,
            provider=_Provider(),
            pit_store=_Store(),
            tape_policy=_tape_policy(),
            bridge_policy=ProspectiveBtcProofBridgePolicy(
                historical_memory_lookback_hours=168,
            ),
        )

        self.assertEqual(result["status"], "PROSPECTIVE_PROOF_DECISION_FROZEN")
        self.assertTrue(result["historical_memory_available"])
        self.assertGreaterEqual(int(result["historical_memory_analogue_count"]), 12)
        decision = result["frozen_thesis"]["decision"]
        self.assertIn("HISTORICAL_MEMORY", decision["available_lanes"])
        self.assertNotIn("HISTORICAL_MEMORY", decision["missing_lanes"])
        self.assertFalse(result["trade_generated"])
        self.assertFalse(result["futures_trade_generated"])

    async def test_memory_remains_missing_when_only_short_history_exists(self):
        provider = _Provider()
        provider.hourly = provider.hourly[-40:]
        result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="memory-bridge-short",
            decision_at=DECISION,
            provider=provider,
            pit_store=_Store(),
            tape_policy=_tape_policy(),
            bridge_policy=ProspectiveBtcProofBridgePolicy(
                historical_memory_lookback_hours=48,
            ),
        )

        self.assertFalse(result["historical_memory_available"])
        decision = result["frozen_thesis"]["decision"]
        self.assertIn("HISTORICAL_MEMORY", decision["missing_lanes"])


if __name__ == "__main__":
    unittest.main()
