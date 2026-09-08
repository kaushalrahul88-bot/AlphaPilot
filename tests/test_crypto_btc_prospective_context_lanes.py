import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow, HistoricalProvenance
from app.crypto_btc_prospective_proof_bridge import freeze_prospective_btc_thesis_from_existing_sources
from app.crypto_btc_prospective_thesis_tape import ProspectiveBtcThesisTapePolicy
from app.crypto_deribit_options_pit import DATASET as DERIBIT_CONTEXT_DATASET
from app.crypto_stablecoin_pit_capture import STABLECOIN_SUPPLY_DATASET

UTC = timezone.utc
DECISION_AT = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)


def _provenance(source_id):
    return HistoricalProvenance(
        provider="COINDCX",
        source_id=source_id,
        availability_basis="BAR_COMPLETION_RECONSTRUCTION",
        point_in_time_proven=True,
        reconstructible_public_data=True,
    )


def _candle(*, available_at, close, minutes=60, source_id="c"):
    return BtcSpotCandleArchiveRow(
        open_at=available_at - timedelta(minutes=minutes),
        close_at=available_at,
        available_at=available_at,
        open=close - 40.0,
        high=close + 10.0,
        low=close - 90.0,
        close=close,
        volume=100.0,
        provenance=_provenance(source_id),
    ).validated()


def _structure_rows(decision_at=DECISION_AT):
    return [
        _candle(
            available_at=decision_at - timedelta(hours=30 - i),
            close=97_000.0 + i * 100.0,
            source_id=f"structure-{i}",
        )
        for i in range(31)
    ]


def _decision_rows(decision_at=DECISION_AT):
    return [
        _candle(
            available_at=decision_at,
            close=100_000.0,
            minutes=1,
            source_id="decision-price",
        )
    ]


def _deribit_row(first_seen_at):
    return {
        "dataset": DERIBIT_CONTEXT_DATASET,
        "provider": "DERIBIT",
        "source_key": f"deribit-{first_seen_at.isoformat()}",
        "first_seen_at": first_seen_at.isoformat(),
        "payload": {
            "atm_mark_iv_pct": 55.0,
            "put_call_open_interest_ratio": 1.05,
            "term_structure_slope_iv_points": 2.0,
            "global_options_context_only": True,
        },
    }


def _stablecoin_row(first_seen_at, total=200_000_000_000.0):
    return {
        "dataset": STABLECOIN_SUPPLY_DATASET,
        "provider": "DEFILLAMA",
        "source_key": f"stablecoin-{first_seen_at.isoformat()}",
        "first_seen_at": first_seen_at.isoformat(),
        "payload": {"total_circulating": total},
    }


class _VisibleStore:
    def __init__(self, rows):
        self.rows = list(rows)

    async def visible_as_of(self, as_of):
        return [
            row
            for row in self.rows
            if datetime.fromisoformat(str(row["first_seen_at"]).replace("Z", "+00:00")) <= as_of
        ]


class _Provider:
    def __init__(self, decision_at=DECISION_AT):
        self.decision_at = decision_at
        self.structure = _structure_rows(decision_at)
        self.decision = _decision_rows(decision_at)

    def fetch_spot_candles(self, *, interval, start_at=None, end_at=None, limit=1000):
        if interval == "1h":
            return list(self.structure)
        return list(self.decision)


def _tape_policy():
    return ProspectiveBtcThesisTapePolicy(
        trade_horizon="intraday",
        evaluation_horizon_hours=1.0,
        terminal_price_max_gap_seconds=60,
        neutral_band_pct=0.25,
        large_move_threshold_pct=1.5,
    ).validated()


class ProspectiveBtcContextLaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_existing_pit_context_lanes_are_admitted(self):
        store = _VisibleStore([
            _deribit_row(DECISION_AT - timedelta(minutes=1)),
            _stablecoin_row(DECISION_AT - timedelta(minutes=30)),
        ])
        result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="context-ready",
            decision_at=DECISION_AT,
            provider=_Provider(),
            pit_store=store,
            tape_policy=_tape_policy(),
        )

        decision = result["frozen_thesis"]["decision"]
        self.assertTrue(result["options_context_available"])
        self.assertEqual(result["options_context_status"], "DERIBIT_OPTIONS_CONTEXT_READY")
        self.assertTrue(result["stablecoin_context_available"])
        self.assertEqual(result["stablecoin_context_status"], "FRESH_INSUFFICIENT_COMPARISON_HISTORY")
        self.assertIn("OPTIONS_MARKET", decision["available_lanes"])
        self.assertIn("STABLECOIN_LIQUIDITY", decision["available_lanes"])
        self.assertFalse(result["options_contract_data_used"])
        self.assertFalse(result["options_pnl_measured"])
        self.assertFalse(result["trade_generated"])

    async def test_context_first_seen_after_decision_stays_missing(self):
        future_rows = [
            _deribit_row(DECISION_AT + timedelta(minutes=1)),
            _stablecoin_row(DECISION_AT + timedelta(minutes=1)),
        ]
        result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="historical-before-context",
            decision_at=DECISION_AT,
            provider=_Provider(),
            pit_store=_VisibleStore(future_rows),
            tape_policy=_tape_policy(),
        )

        decision = result["frozen_thesis"]["decision"]
        self.assertFalse(result["options_context_available"])
        self.assertFalse(result["stablecoin_context_available"])
        self.assertIn("OPTIONS_MARKET", decision["missing_lanes"])
        self.assertIn("STABLECOIN_LIQUIDITY", decision["missing_lanes"])
        self.assertNotIn("OPTIONS_MARKET", decision["available_lanes"])
        self.assertNotIn("STABLECOIN_LIQUIDITY", decision["available_lanes"])

    async def test_stale_context_does_not_mark_lanes_available(self):
        store = _VisibleStore([
            _deribit_row(DECISION_AT - timedelta(minutes=20)),
            _stablecoin_row(DECISION_AT - timedelta(hours=30), 198_000_000_000.0),
            _stablecoin_row(DECISION_AT - timedelta(hours=3), 200_000_000_000.0),
        ])
        result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="stale-context",
            decision_at=DECISION_AT,
            provider=_Provider(),
            pit_store=store,
            tape_policy=_tape_policy(),
        )

        decision = result["frozen_thesis"]["decision"]
        self.assertFalse(result["options_context_available"])
        self.assertEqual(result["options_context_status"], "STALE_DERIBIT_OPTIONS_CONTEXT")
        self.assertFalse(result["stablecoin_context_available"])
        self.assertEqual(result["stablecoin_context_status"], "STALE")
        self.assertIn("OPTIONS_MARKET", decision["missing_lanes"])
        self.assertIn("STABLECOIN_LIQUIDITY", decision["missing_lanes"])


if __name__ == "__main__":
    unittest.main()
