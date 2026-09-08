from datetime import datetime, timedelta, timezone

from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow, HistoricalProvenance
from app.crypto_btc_intraday_microstructure import (
    architecture_contract,
    derive_btc_intraday_microstructure_evidence,
    reconcile_spot_regime_with_intraday_timing,
)
from app.crypto_market_intelligence import Evidence, assemble_market_state

UTC = timezone.utc
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _row(open_at: datetime, open_price: float, close_price: float, *, volume: float = 100.0):
    high = max(open_price, close_price) + 0.25
    low = min(open_price, close_price) - 0.25
    return BtcSpotCandleArchiveRow(
        open_at=open_at,
        close_at=open_at + timedelta(minutes=1),
        available_at=open_at + timedelta(minutes=1),
        open=open_price,
        high=high,
        low=low,
        close=close_price,
        volume=volume,
        provenance=HistoricalProvenance(
            provider="COINDCX",
            source_id=f"test:{int(open_at.timestamp())}",
            availability_basis="BAR_COMPLETION_RECONSTRUCTION",
            point_in_time_proven=True,
            reconstructible_public_data=True,
        ),
    ).validated()


def _trend_rows(*, bullish: bool = True):
    rows = []
    base = 100.0
    for index in range(45):
        open_at = NOW - timedelta(minutes=45 - index)
        move = index * 0.08 * (1 if bullish else -1)
        open_price = base + move
        close_price = open_price + (0.06 if bullish else -0.06)
        rows.append(_row(open_at, open_price, close_price))
    return rows


def _regime(stance: str) -> Evidence:
    return Evidence(
        family="BTC_SPOT_STRUCTURE",
        causal_origin="SPOT_PRICE_STRUCTURE",
        stance=stance,
        strength="MEDIUM",
        confidence=0.72,
        observed_at=NOW - timedelta(minutes=30),
        reason="broad test regime",
        context_only=stance not in {"BULLISH", "BEARISH"},
        source="COINDCX_PUBLIC_SPOT_CANDLES",
        metadata={"return_1h_pct": 1.0},
    )


def _positioning(stance: str) -> Evidence:
    return Evidence(
        family="DERIVATIVES_POSITIONING",
        causal_origin="LEVERAGED_POSITIONING",
        stance=stance,
        strength="MEDIUM",
        confidence=0.75,
        observed_at=NOW - timedelta(minutes=5),
        reason="independent OI test",
        context_only=False,
        source="DELTA_EXCHANGE_INDIA_OI_HISTORY",
        metadata={},
    )


def test_bullish_completed_microstructure_is_directional_and_same_spot_origin():
    evidence = derive_btc_intraday_microstructure_evidence(_trend_rows(bullish=True), decision_at=NOW)
    assert evidence is not None
    assert evidence.stance == "BULLISH"
    assert evidence.causal_origin == "SPOT_PRICE_STRUCTURE"
    assert evidence.metadata["completed_1m_candles_only"] is True
    assert evidence.metadata["independent_confirmation"] is False
    assert evidence.metadata["return_5m_pct"] > 0
    assert evidence.metadata["return_15m_pct"] > 0
    assert evidence.metadata["vote_score"] >= 4


def test_bearish_completed_microstructure_is_directional():
    evidence = derive_btc_intraday_microstructure_evidence(_trend_rows(bullish=False), decision_at=NOW)
    assert evidence is not None
    assert evidence.stance == "BEARISH"
    assert evidence.metadata["return_5m_pct"] < 0
    assert evidence.metadata["return_15m_pct"] < 0
    assert evidence.metadata["vote_score"] <= -4


def test_future_incomplete_bar_cannot_change_intraday_timing():
    rows = _trend_rows(bullish=True)
    baseline = derive_btc_intraday_microstructure_evidence(rows, decision_at=NOW)
    future_open = NOW
    future = _row(future_open, 50.0, 500.0, volume=1_000_000.0)
    with_future = derive_btc_intraday_microstructure_evidence(rows + [future], decision_at=NOW)
    assert baseline is not None and with_future is not None
    assert with_future.stance == baseline.stance
    assert with_future.metadata["vote_score"] == baseline.metadata["vote_score"]
    assert with_future.metadata["latest_available_at"] == baseline.metadata["latest_available_at"]


def test_fresh_opposite_timing_vetoes_stale_broad_spot_regime():
    timing = derive_btc_intraday_microstructure_evidence(_trend_rows(bullish=False), decision_at=NOW)
    reconciled = reconcile_spot_regime_with_intraday_timing(_regime("BULLISH"), timing, decision_at=NOW)
    assert reconciled is not None
    assert reconciled.stance == "UNKNOWN"
    assert reconciled.context_only is True
    assert reconciled.causal_origin == "SPOT_PRICE_STRUCTURE"
    assert reconciled.metadata["broad_regime_stance"] == "BULLISH"
    assert reconciled.metadata["intraday_timing_stance"] == "BEARISH"


def test_missing_timing_does_not_repeat_directional_regime_for_v2_15m():
    reconciled = reconcile_spot_regime_with_intraday_timing(_regime("BULLISH"), None, decision_at=NOW)
    assert reconciled is not None
    assert reconciled.stance == "UNKNOWN"
    assert reconciled.context_only is True
    assert reconciled.metadata["timing_confirmation_required_for_v2_15m"] is True


def test_aligned_timing_and_regime_collapse_to_exactly_one_spot_origin():
    timing = derive_btc_intraday_microstructure_evidence(_trend_rows(bullish=True), decision_at=NOW)
    broad = _regime("BULLISH")
    reconciled = reconcile_spot_regime_with_intraday_timing(broad, timing, decision_at=NOW)
    assert reconciled is not None
    assert reconciled.stance == "BULLISH"
    assert reconciled.context_only is False
    assert reconciled.causal_origin == "SPOT_PRICE_STRUCTURE"
    assert reconciled.metadata["same_causal_origin_reconciled"] is True

    # Raw broad + timing rows still cannot satisfy the production two-origin gate.
    duplicate_spot_state = assemble_market_state([broad, timing], decision_at=NOW, trade_horizon="intraday")
    assert duplicate_spot_state["direction"] == "UNKNOWN"
    assert len(duplicate_spot_state["counted_evidence"]) == 1

    # A genuinely independent OI positioning origin can provide confirmation.
    confirmed = assemble_market_state([reconciled, _positioning("BULLISH")], decision_at=NOW, trade_horizon="intraday")
    assert confirmed["direction"] == "BULLISH"
    assert len(confirmed["counted_evidence"]) == 2


def test_microstructure_is_deterministic_for_frozen_inputs():
    rows = _trend_rows(bullish=True)
    first = derive_btc_intraday_microstructure_evidence(rows, decision_at=NOW)
    second = derive_btc_intraday_microstructure_evidence(list(reversed(rows)), decision_at=NOW)
    assert first == second


def test_microstructure_contract_keeps_execution_and_independence_disabled():
    contract = architecture_contract()
    assert contract["completed_candles_only"] is True
    assert contract["causal_origin"] == "SPOT_PRICE_STRUCTURE"
    assert contract["independent_confirmation_created"] is False
    assert contract["fresh_timing_may_veto_broad_regime"] is True
    assert contract["timing_without_broad_regime_may_create_direction"] is False
    assert contract["outcome_data_used"] is False
    assert contract["production_two_origin_gate_changed"] is False
    assert contract["options_trade_generated"] is False
    assert contract["futures_trade_generated"] is False
    assert contract["live_execution"] is False
