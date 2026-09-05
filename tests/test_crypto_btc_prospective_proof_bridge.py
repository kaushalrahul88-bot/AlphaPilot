import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_derivatives_capture import BTC_LIQUIDATIONS_DATASET, BTC_OPEN_INTEREST_DATASET
from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow, HistoricalProvenance
from app.crypto_btc_prospective_proof_bridge import (
    ProspectiveBtcProofBridgePolicy,
    architecture_contract,
    freeze_prospective_btc_thesis_from_existing_sources,
    resolve_prospective_btc_thesis_from_coindcx,
)
from app.crypto_btc_prospective_thesis_tape import ProspectiveBtcThesisTapePolicy

UTC = timezone.utc
DECISION_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _provenance(source_id):
    return HistoricalProvenance(
        provider="COINDCX",
        source_id=source_id,
        availability_basis="BAR_COMPLETION_RECONSTRUCTION",
        point_in_time_proven=True,
        reconstructible_public_data=True,
    )


def _candle(*, available_at, close, volume=100.0, minutes=60, source_id="c"):
    return BtcSpotCandleArchiveRow(
        open_at=available_at - timedelta(minutes=minutes),
        close_at=available_at,
        available_at=available_at,
        open=close - 40.0,
        high=close + 10.0,
        low=close - 90.0,
        close=close,
        volume=volume,
        provenance=_provenance(source_id),
    ).validated()


def _structure_rows(direction="BULLISH"):
    rows = []
    for i in range(31):
        available = DECISION_AT - timedelta(hours=30 - i)
        if direction == "BULLISH":
            close = 97_000.0 + i * 100.0
        else:
            close = 103_000.0 - i * 100.0
        row = _candle(
            available_at=available,
            close=close,
            volume=250.0 if available == DECISION_AT else 100.0,
            minutes=60,
            source_id=f"h-{direction}-{i}",
        )
        if direction == "BEARISH":
            row = BtcSpotCandleArchiveRow(
                open_at=row.open_at,
                close_at=row.close_at,
                available_at=row.available_at,
                open=close + 40.0,
                high=close + 90.0,
                low=close - 10.0,
                close=close,
                volume=row.volume,
                provenance=row.provenance,
            ).validated()
        rows.append(row)
    return rows


def _decision_rows(price=100_000.0):
    return [_candle(
        available_at=DECISION_AT,
        close=price,
        volume=10.0,
        minutes=1,
        source_id="decision-1m",
    )]


def _outcome_rows(return_pct=2.0, terminal=True):
    rows = [_candle(
        available_at=DECISION_AT + timedelta(minutes=30),
        close=100_500.0 if return_pct >= 0 else 99_500.0,
        volume=10.0,
        minutes=1,
        source_id="outcome-30m",
    )]
    if terminal:
        rows.append(_candle(
            available_at=DECISION_AT + timedelta(hours=1),
            close=100_000.0 * (1 + return_pct / 100.0),
            volume=10.0,
            minutes=1,
            source_id="outcome-60m",
        ))
    return rows


def _pit_rows(direction="BULLISH"):
    if direction == "BULLISH":
        oi_open, oi_close = 100_000_000.0, 110_000_000.0
        long_liq, short_liq = 1_000_000.0, 2_000_000.0
    else:
        oi_open, oi_close = 100_000_000.0, 110_000_000.0
        long_liq, short_liq = 2_000_000.0, 1_000_000.0
    event = DECISION_AT - timedelta(minutes=1)
    return [
        {
            "dataset": BTC_OPEN_INTEREST_DATASET,
            "provider": "COINGLASS",
            "source_key": "oi",
            "event_at": event.isoformat(),
            "first_seen_at": event.isoformat(),
            "payload": {
                "interval": "1h",
                "open_interest_open_usd": oi_open,
                "open_interest_close_usd": oi_close,
            },
        },
        {
            "dataset": BTC_LIQUIDATIONS_DATASET,
            "provider": "COINGLASS",
            "source_key": "liq",
            "event_at": event.isoformat(),
            "first_seen_at": event.isoformat(),
            "payload": {
                "interval": "1h",
                "long_liquidation_usd": long_liq,
                "short_liquidation_usd": short_liq,
            },
        },
    ]


class _Store:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    async def visible_as_of(self, as_of):
        self.calls.append(as_of)
        return list(self.rows)


class _Provider:
    def __init__(self, *, structure=None, decision=None, outcome=None):
        self.structure = list(structure or _structure_rows())
        self.decision = list(decision or _decision_rows())
        self.outcome = list(outcome or _outcome_rows())
        self.calls = []

    def fetch_spot_candles(self, *, interval, start_at=None, end_at=None, limit=1000):
        self.calls.append({"interval": interval, "start_at": start_at, "end_at": end_at, "limit": limit})
        if interval == "1h":
            return list(self.structure)
        if end_at is not None and end_at <= DECISION_AT:
            return list(self.decision)
        return list(self.outcome)


def _tape_policy(horizon=1.0, terminal_gap=60):
    return ProspectiveBtcThesisTapePolicy(
        trade_horizon="intraday",
        evaluation_horizon_hours=horizon,
        terminal_price_max_gap_seconds=terminal_gap,
        neutral_band_pct=0.25,
        large_move_threshold_pct=1.5,
    ).validated()


class ProspectiveBtcProofBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_coindcx_and_pit_sources_freeze_bullish_thesis(self):
        provider = _Provider()
        store = _Store(_pit_rows("BULLISH"))
        result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="real-proof-001",
            decision_at=DECISION_AT,
            provider=provider,
            pit_store=store,
            tape_policy=_tape_policy(),
        )
        self.assertEqual(result["status"], "PROSPECTIVE_PROOF_DECISION_FROZEN")
        self.assertEqual(result["decision_btc_price"], 100_000.0)
        self.assertEqual(result["frozen_thesis"]["decision"]["market_direction"], "BULLISH")
        self.assertEqual(result["derivatives_evidence_status"], "BULLISH")
        self.assertEqual(result["pit_record_count"], 2)
        self.assertEqual(len(store.calls), 1)
        self.assertEqual(len(provider.calls), 2)
        self.assertFalse(result["options_contract_data_used"])
        self.assertFalse(result["options_pnl_measured"])
        self.assertFalse(result["futures_trade_generated"])
        self.assertFalse(result["trade_generated"])

    async def test_empty_derivatives_pit_produces_abstention_not_synthetic_confirmation(self):
        result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="no-derivatives",
            decision_at=DECISION_AT,
            provider=_Provider(),
            pit_store=_Store([]),
            tape_policy=_tape_policy(),
        )
        self.assertEqual(result["status"], "PROSPECTIVE_PROOF_DECISION_FROZEN")
        self.assertEqual(result["frozen_thesis"]["decision"]["market_direction"], "UNKNOWN")
        self.assertEqual(result["derivatives_evidence_status"], "UNKNOWN")
        self.assertEqual(result["pit_record_count"], 0)

    async def test_future_pit_row_is_rejected_even_if_store_returns_it(self):
        rows = _pit_rows()
        rows[0]["first_seen_at"] = (DECISION_AT + timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):
            await freeze_prospective_btc_thesis_from_existing_sources(
                click_id="bad-store",
                decision_at=DECISION_AT,
                provider=_Provider(),
                pit_store=_Store(rows),
                tape_policy=_tape_policy(),
            )

    async def test_stale_decision_price_fails_closed_without_frozen_thesis(self):
        stale = [_candle(
            available_at=DECISION_AT - timedelta(minutes=5),
            close=100_000.0,
            volume=10.0,
            minutes=1,
            source_id="stale",
        )]
        result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="stale-price",
            decision_at=DECISION_AT,
            provider=_Provider(decision=stale),
            pit_store=_Store(_pit_rows()),
            tape_policy=_tape_policy(),
        )
        self.assertEqual(result["status"], "PROOF_INPUT_UNRESOLVED")
        self.assertEqual(result["reason"], "BTC_DECISION_PRICE_MISSING_OR_STALE")
        self.assertIsNone(result["frozen_thesis"])

    async def test_insufficient_structure_fails_closed(self):
        short_history = _structure_rows()[-5:]
        result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="short-structure",
            decision_at=DECISION_AT,
            provider=_Provider(structure=short_history),
            pit_store=_Store(_pit_rows()),
            tape_policy=_tape_policy(),
        )
        self.assertEqual(result["status"], "PROOF_INPUT_UNRESOLVED")
        self.assertEqual(result["reason"], "BTC_SPOT_STRUCTURE_UNAVAILABLE")
        self.assertIsNone(result["frozen_thesis"])

    async def test_due_resolution_uses_completed_coindcx_btc_only(self):
        provider = _Provider(outcome=_outcome_rows(2.0))
        frozen_result = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="resolve-hit",
            decision_at=DECISION_AT,
            provider=provider,
            pit_store=_Store(_pit_rows()),
            tape_policy=_tape_policy(),
        )
        before_calls = len(provider.calls)
        resolved = await resolve_prospective_btc_thesis_from_coindcx(
            frozen_record=frozen_result["frozen_thesis"],
            resolution_at=DECISION_AT + timedelta(hours=1),
            provider=provider,
        )
        self.assertEqual(resolved["status"], "THESIS_OUTCOME_RESOLVED")
        self.assertEqual(resolved["outcome"]["classification"], "DIRECTIONAL_HIT")
        self.assertEqual(resolved["outcome_source"], "COINDCX_PUBLIC_COMPLETED_SPOT_CANDLES")
        self.assertEqual(resolved["completed_btc_candle_count"], 2)
        self.assertEqual(len(provider.calls), before_calls + 1)
        self.assertFalse(resolved["options_pnl_measured"])
        self.assertFalse(resolved["futures_trade_generated"])
        self.assertFalse(resolved["trade_generated"])

    async def test_early_resolution_makes_no_provider_call(self):
        provider = _Provider()
        frozen = (await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="early",
            decision_at=DECISION_AT,
            provider=provider,
            pit_store=_Store(_pit_rows()),
            tape_policy=_tape_policy(),
        ))["frozen_thesis"]
        before = len(provider.calls)
        result = await resolve_prospective_btc_thesis_from_coindcx(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(minutes=59),
            provider=provider,
        )
        self.assertEqual(result["status"], "THESIS_OUTCOME_NOT_DUE")
        self.assertFalse(result["provider_called"])
        self.assertEqual(len(provider.calls), before)

    async def test_missing_terminal_completed_bar_stays_unresolved(self):
        provider = _Provider(outcome=_outcome_rows(2.0, terminal=False))
        frozen = (await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="missing-terminal",
            decision_at=DECISION_AT,
            provider=provider,
            pit_store=_Store(_pit_rows()),
            tape_policy=_tape_policy(),
        ))["frozen_thesis"]
        result = await resolve_prospective_btc_thesis_from_coindcx(
            frozen_record=frozen,
            resolution_at=DECISION_AT + timedelta(hours=1),
            provider=provider,
        )
        self.assertEqual(result["status"], "THESIS_OUTCOME_UNRESOLVED")
        self.assertEqual(result["outcome"]["reason"], "TERMINAL_BTC_PRICE_TOO_FAR_FROM_HORIZON_END")

    async def test_outcome_interval_cannot_silently_exceed_coindcx_limit(self):
        provider = _Provider()
        frozen = (await freeze_prospective_btc_thesis_from_existing_sources(
            click_id="long-horizon",
            decision_at=DECISION_AT,
            provider=provider,
            pit_store=_Store(_pit_rows()),
            tape_policy=_tape_policy(horizon=20.0, terminal_gap=60),
        ))["frozen_thesis"]
        with self.assertRaises(ValueError):
            await resolve_prospective_btc_thesis_from_coindcx(
                frozen_record=frozen,
                resolution_at=DECISION_AT + timedelta(hours=20),
                provider=provider,
                bridge_policy=ProspectiveBtcProofBridgePolicy(outcome_interval="1m"),
            )

    def test_architecture_reuses_sources_without_adding_automatic_collection(self):
        contract = architecture_contract()
        self.assertFalse(contract["new_provider_added"])
        self.assertFalse(contract["new_scheduler_added"])
        self.assertFalse(contract["new_database_schema_added"])
        self.assertFalse(contract["automatic_startup_added"])
        self.assertTrue(contract["explicit_invocation_required"])
        self.assertTrue(contract["decision_uses_only_pit_visible_rows"])
        self.assertTrue(contract["outcome_uses_only_completed_coindcx_candles"])
        self.assertFalse(contract["derivatives_missing_equals_neutral_vote"])
        self.assertFalse(contract["oi_liquidations_may_be_fabricated"])
        self.assertFalse(contract["options_contract_data_required"])
        self.assertFalse(contract["options_pnl_measured"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["live_execution"])


if __name__ == "__main__":
    unittest.main()
