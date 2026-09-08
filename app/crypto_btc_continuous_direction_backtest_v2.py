"""BTC 15-minute direction replay V2 with fresh spot timing and repaired Delta OI.

V2 is an explicitly in-sample development diagnostic over the exact frozen
282-click window used by V1. It does not claim edge. The broad 1h/4h/24h spot
regime is reconciled with completed 1-minute microstructure before the single
``SPOT_PRICE_STRUCTURE`` origin is submitted to the information board. Delta
OI remains a genuinely separate ``LEVERAGED_POSITIONING`` origin.

No option contract, premium, P&L, Futures trade, order or capital is created.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from app.coindcx_btc_public_provider import CoinDcxBtcProviderPolicy, CoinDcxBtcPublicProvider
from app.crypto_btc_continuous_direction_backtest import (
    CLICK_STEP_MINUTES,
    DIAGNOSTIC_HORIZONS_MINUTES,
    PRIMARY_HORIZON_MINUTES,
    ProgressCallback,
    _CachedCoinDcx,
    _FrozenPitRows,
    _classification,
    _fetch_spot,
    _progress,
    _structure_prefetch_hours,
    _summary,
    _terminal_outcome,
    _utc,
)
from app.crypto_btc_derivatives_evidence import derivatives_evidence_from_full_pit_context
from app.crypto_btc_historical_analogue import (
    BtcHistoricalAnaloguePolicy,
    derive_btc_historical_analogue_evidence,
)
from app.crypto_btc_historical_data_adapter import BtcHistoricalArchive, derive_spot_structure_evidence
from app.crypto_btc_information_board import build_btc_information_board
from app.crypto_btc_intraday_microstructure import (
    derive_btc_intraday_microstructure_evidence,
    reconcile_spot_regime_with_intraday_timing,
)
from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_btc_prospective_proof_bridge import (
    ProspectiveBtcProofBridgePolicy,
    _latest_price,
    _options_context_is_available,
    _price_change_pct,
    _reconcile_positioning_evidence,
    _stablecoin_context_is_available,
    _stablecoin_context_status,
    _visible_completed,
)
from app.crypto_btc_research_direction import derive_btc_research_directional_lean
from app.crypto_deribit_options_evidence import deribit_options_evidence_from_pit_records
from app.crypto_stablecoin_liquidity import aggregate_stablecoin_liquidity_context
from app.delta_india_btc_derivatives_context import (
    DeltaIndiaBtcDerivativesContextPolicy,
    DeltaIndiaBtcDerivativesPublicProvider,
    derive_delta_oi_positioning_evidence,
)

UTC = timezone.utc
MODE = "BTC_CONTINUOUS_PIT_15M_DIRECTION_REPLAY_V2"
VERSION = "BTC_PERCEPTION_V2_INTRADAY_TIMING_DELTA_OI"

# Exact V1 development sample. These bounds are intentionally immutable so a
# V2 comparison cannot silently gain extra later PIT observations.
DEVELOPMENT_WINDOW_START = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)
DEVELOPMENT_WINDOW_END_EXCLUSIVE = datetime(2026, 9, 8, 16, 30, tzinfo=UTC)
DEVELOPMENT_LAST_CLICK = DEVELOPMENT_WINDOW_END_EXCLUSIVE - timedelta(minutes=CLICK_STEP_MINUTES)
EXPECTED_DEVELOPMENT_CLICKS = 282
ONE_MINUTE_PREFETCH_MINUTES = 60


def _development_clicks() -> list[datetime]:
    clicks: list[datetime] = []
    cursor = DEVELOPMENT_WINDOW_START
    while cursor < DEVELOPMENT_WINDOW_END_EXCLUSIVE:
        clicks.append(cursor)
        cursor += timedelta(minutes=CLICK_STEP_MINUTES)
    if len(clicks) != EXPECTED_DEVELOPMENT_CLICKS:
        raise RuntimeError("BTC V2 frozen development grid no longer equals 282 clicks")
    return clicks


async def continuous_direction_v2_readiness(database_url: str) -> dict[str, Any]:
    store = PostgresBtcPitArchiveStore(database_url)
    rows = await store.visible_as_of(DEVELOPMENT_LAST_CLICK)
    usable = [
        row for row in rows
        if str(row.get("first_seen_at") or "")
        and _utc(datetime.fromisoformat(str(row["first_seen_at"]).replace("Z", "+00:00"))) <= DEVELOPMENT_LAST_CLICK
    ]
    first_seen = None
    last_seen = None
    if usable:
        times = [
            _utc(datetime.fromisoformat(str(row["first_seen_at"]).replace("Z", "+00:00")))
            for row in usable
        ]
        first_seen = min(times)
        last_seen = max(times)
    ready = bool(first_seen is not None and first_seen <= DEVELOPMENT_WINDOW_START and last_seen is not None and last_seen >= DEVELOPMENT_LAST_CLICK)
    reason = None if ready else "BTC_V2_FROZEN_DEVELOPMENT_PIT_WINDOW_UNAVAILABLE"
    return {
        "version": "BTC_CONTINUOUS_DIRECTION_READINESS_V2",
        "mode": MODE,
        "ready": ready,
        "reason": reason,
        "window_start": DEVELOPMENT_WINDOW_START.isoformat(),
        "window_end_exclusive": DEVELOPMENT_WINDOW_END_EXCLUSIVE.isoformat(),
        "last_click_at": DEVELOPMENT_LAST_CLICK.isoformat(),
        "scheduled_clicks": EXPECTED_DEVELOPMENT_CLICKS,
        "click_interval_minutes": CLICK_STEP_MINUTES,
        "pit_rows_visible_by_last_click": len(usable),
        "first_pit_seen_at": None if first_seen is None else first_seen.isoformat(),
        "last_pit_seen_at": None if last_seen is None else last_seen.isoformat(),
        "frozen_to_v1_development_sample": True,
        "development_same_sample": True,
        "edge_claim_allowed": False,
        "research_only": True,
        "production_two_origin_gate_changed": False,
        "options_profitability_evaluated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed_inr": 0,
    }


def _v2_diagnostics(clicks: list[dict[str, Any]], *, oi_rows: list[Any]) -> dict[str, Any]:
    timing = Counter(str(row.get("intraday_timing_status") or "MISSING") for row in clicks)
    broad = Counter(str(row.get("broad_spot_regime_status") or "MISSING") for row in clicks)
    reconciled = Counter(str(row.get("reconciled_spot_status") or "MISSING") for row in clicks)
    oi = Counter(str(row.get("delta_oi_evidence_status") or "MISSING") for row in clicks)

    broad_directional = [row for row in clicks if row.get("broad_spot_regime_status") in {"BULLISH", "BEARISH"}]
    confirmations = sum(
        row.get("broad_spot_regime_status") in {"BULLISH", "BEARISH"}
        and row.get("intraday_timing_status") == row.get("broad_spot_regime_status")
        for row in clicks
    )
    contradictions = sum(
        row.get("broad_spot_regime_status") in {"BULLISH", "BEARISH"}
        and row.get("intraday_timing_status") in {"BULLISH", "BEARISH"}
        and row.get("intraday_timing_status") != row.get("broad_spot_regime_status")
        for row in clicks
    )
    timing_missing_or_neutral_blocks = sum(
        row.get("broad_spot_regime_status") in {"BULLISH", "BEARISH"}
        and row.get("intraday_timing_status") not in {"BULLISH", "BEARISH"}
        for row in clicks
    )
    unique_broad_states = {
        (str(row.get("broad_spot_observed_at") or ""), str(row.get("broad_spot_regime_status") or ""))
        for row in broad_directional
    }
    emitted_spot = [row for row in clicks if row.get("reconciled_spot_status") in {"BULLISH", "BEARISH"}]
    oi_directional = sum(row.get("delta_oi_evidence_status") in {"BULLISH", "BEARISH"} for row in clicks)
    return {
        "intraday_timing_states": dict(sorted(timing.items())),
        "broad_spot_regime_states": dict(sorted(broad.items())),
        "reconciled_spot_states": dict(sorted(reconciled.items())),
        "delta_oi_evidence_states": dict(sorted(oi.items())),
        "broad_directional_clicks": len(broad_directional),
        "broad_timing_confirmations": confirmations,
        "broad_timing_contradictions": contradictions,
        "broad_directional_blocked_by_missing_or_neutral_timing": timing_missing_or_neutral_blocks,
        "unique_directional_broad_spot_states": len(unique_broad_states),
        "directional_reconciled_spot_clicks": len(emitted_spot),
        "delta_oi_5m_candles_loaded": len(oi_rows),
        "directional_delta_oi_clicks": oi_directional,
        "same_origin_spot_double_count_allowed": False,
        "development_same_sample": True,
        "edge_claim_allowed": False,
    }


async def run_continuous_direction_v2_15m(
    database_url: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    readiness = await continuous_direction_v2_readiness(database_url)
    if readiness.get("ready") is not True:
        raise ValueError(str(readiness.get("reason") or "BTC V2 frozen development replay is not ready"))

    clicks = _development_clicks()
    start = DEVELOPMENT_WINDOW_START
    end = DEVELOPMENT_LAST_CLICK
    total = len(clicks)
    await _progress(progress_callback, 0, total, "LOADING_FROZEN_INPUTS_V2")

    bridge_policy = ProspectiveBtcProofBridgePolicy().validated()
    structure_prefetch_hours = _structure_prefetch_hours(bridge_policy)
    public = CoinDcxBtcPublicProvider(CoinDcxBtcProviderPolicy(enabled=True, timeout_seconds=25))
    price_end = end + timedelta(minutes=max(DIAGNOSTIC_HORIZONS_MINUTES) + 2)
    one_hour, one_minute = await asyncio.gather(
        asyncio.to_thread(
            _fetch_spot,
            public,
            interval="1h",
            start=start - timedelta(hours=structure_prefetch_hours),
            end=price_end,
        ),
        asyncio.to_thread(
            _fetch_spot,
            public,
            interval="1m",
            start=start - timedelta(minutes=ONE_MINUTE_PREFETCH_MINUTES),
            end=price_end,
        ),
    )
    cached = _CachedCoinDcx({"1h": one_hour, "1m": one_minute})

    pit_store = PostgresBtcPitArchiveStore(database_url)
    frozen_pit_rows = await pit_store.visible_as_of(end)
    frozen_pit = _FrozenPitRows(frozen_pit_rows)

    oi_rows: list[Any] = []
    oi_load_error = None
    try:
        oi_provider = DeltaIndiaBtcDerivativesPublicProvider(
            DeltaIndiaBtcDerivativesContextPolicy(enabled=True, timeout_seconds=25, resolution="5m")
        )
        oi_rows = await asyncio.to_thread(
            oi_provider.fetch_oi_candles,
            start_at=start - timedelta(hours=2, minutes=15),
            end_at=end,
            resolution="5m",
        )
    except Exception as exc:
        oi_load_error = f"{exc.__class__.__name__}: {str(exc)[:240]}"
        oi_rows = []

    await _progress(progress_callback, 0, total, "PROCESSING_DIRECTION_CLICKS_V2")
    results: list[dict[str, Any]] = []
    for index, click in enumerate(clicks):
        visible_structure = _visible_completed(one_hour, as_of=click)
        decision_rows = cached.fetch_spot_candles(
            interval="1m",
            start_at=click - timedelta(minutes=ONE_MINUTE_PREFETCH_MINUTES),
            end_at=click,
            limit=ONE_MINUTE_PREFETCH_MINUTES + 10,
        )
        latest = _latest_price(
            decision_rows,
            as_of=click,
            max_age_seconds=int(bridge_policy.decision_price_max_age_seconds),
        )
        if latest is None:
            results.append({
                "click_index": index,
                "decision_at": click.isoformat(),
                "research_direction": "WAIT",
                "research_confidence": "NONE",
                "decision_status": "UNRESOLVED_DECISION_PRICE",
                "production_direction": "UNKNOWN",
                "broad_spot_regime_status": None,
                "broad_spot_observed_at": None,
                "intraday_timing_status": None,
                "intraday_timing_observed_at": None,
                "reconciled_spot_status": None,
                "delta_oi_evidence_status": None,
                "available_lanes": [],
                "missing_lanes": [],
                "outcomes": {},
                "classifications": {},
            })
            await _progress(progress_callback, index + 1, total, "PROCESSING_DIRECTION_CLICKS_V2")
            continue

        archive = BtcHistoricalArchive(spot_candles=tuple(visible_structure)).validated()
        broad_spot = derive_spot_structure_evidence(
            archive,
            decision_at=click,
            max_spot_age_seconds=int(bridge_policy.structure_max_age_seconds),
        )
        intraday_timing = derive_btc_intraday_microstructure_evidence(
            decision_rows,
            decision_at=click,
        )
        reconciled_spot = reconcile_spot_regime_with_intraday_timing(
            broad_spot,
            intraday_timing,
            decision_at=click,
        )
        evidence = [] if reconciled_spot is None else [reconciled_spot]

        # Historical memory remains experience/context and is deliberately built
        # from the broad completed structure history, not from future outcomes.
        memory_evidence = derive_btc_historical_analogue_evidence(
            visible_structure,
            decision_at=click,
            policy=BtcHistoricalAnaloguePolicy(
                lookback_hours=int(bridge_policy.historical_memory_lookback_hours),
            ),
        )
        if memory_evidence is not None:
            evidence.append(memory_evidence)

        price_change = _price_change_pct(
            visible_structure,
            decision_at=click,
            decision_price=float(latest.close),
            lookback_hours=float(bridge_policy.derivatives_price_lookback_hours),
        )
        pit_rows = frozen_pit.visible_as_of(click)
        pit_derivatives = None
        delta_derivatives = None
        derivatives = None
        if price_change is not None:
            pit_derivatives = derivatives_evidence_from_full_pit_context(
                pit_rows,
                decision_at=click,
                price_change_pct=price_change,
                max_event_misalignment_seconds=int(bridge_policy.max_event_misalignment_seconds),
            )
            if oi_rows:
                delta_derivatives = derive_delta_oi_positioning_evidence(
                    oi_rows,
                    decision_at=click,
                    price_change_pct=price_change,
                )
            derivatives = _reconcile_positioning_evidence(pit_derivatives, delta_derivatives)
            if derivatives is not None:
                evidence.append(derivatives)

        options_evidence = deribit_options_evidence_from_pit_records(pit_rows, decision_at=click)
        options_available = _options_context_is_available(options_evidence)
        if options_available:
            evidence.append(options_evidence)

        stablecoin_evidence = aggregate_stablecoin_liquidity_context(pit_rows, decision_at=click)
        stablecoin_available = _stablecoin_context_is_available(stablecoin_evidence)
        if stablecoin_available:
            evidence.append(stablecoin_evidence)

        board = build_btc_information_board(evidence, decision_at=click, trade_horizon="intraday")
        lean = derive_btc_research_directional_lean(evidence, decision_at=click, trade_horizon="intraday")
        production_state = board.get("underlying_market_state") or {}
        production_direction = str(production_state.get("direction") or "UNKNOWN").upper()

        # Direction is frozen before any later price is read for scoring.
        prediction = str(lean["direction"])
        outcomes = {
            str(horizon): _terminal_outcome(
                one_minute,
                decision_at=click,
                entry_price=float(latest.close),
                horizon_minutes=horizon,
            )
            for horizon in DIAGNOSTIC_HORIZONS_MINUTES
        }
        classifications = {key: _classification(prediction, outcome) for key, outcome in outcomes.items()}
        lane_status = board.get("lane_status") or {}
        available_lanes = sorted(lane for lane, row in lane_status.items() if row.get("available") is True)
        results.append({
            "click_index": index,
            "decision_at": click.isoformat(),
            "decision_status": "FROZEN_RESEARCH_DIRECTION_V2",
            "decision_btc_price": float(latest.close),
            "research_direction": prediction,
            "research_confidence": str(lean["confidence_band"]),
            "research_directional_lean": lean,
            "production_direction": production_direction,
            "production_market_state": production_state.get("state"),
            "broad_spot_regime_status": None if broad_spot is None else broad_spot.stance,
            "broad_spot_observed_at": None if broad_spot is None else _utc(broad_spot.observed_at).isoformat(),
            "broad_spot_evidence": None if broad_spot is None else broad_spot.frozen_dict(),
            "intraday_timing_status": None if intraday_timing is None else intraday_timing.stance,
            "intraday_timing_observed_at": None if intraday_timing is None else _utc(intraday_timing.observed_at).isoformat(),
            "intraday_timing_evidence": None if intraday_timing is None else intraday_timing.frozen_dict(),
            "reconciled_spot_status": None if reconciled_spot is None else reconciled_spot.stance,
            "reconciled_spot_evidence": None if reconciled_spot is None else reconciled_spot.frozen_dict(),
            "available_lanes": available_lanes,
            "missing_lanes": sorted(board.get("missing_lanes") or []),
            "pit_record_count_visible": len(pit_rows),
            "derivatives_evidence_status": None if derivatives is None else derivatives.stance,
            "pit_derivatives_evidence_status": None if pit_derivatives is None else pit_derivatives.stance,
            "delta_oi_evidence_status": None if delta_derivatives is None else delta_derivatives.stance,
            "historical_memory_available": memory_evidence is not None,
            "historical_memory_analogue_count": None if memory_evidence is None else memory_evidence.metadata.get("analogue_count"),
            "options_context_available": options_available,
            "stablecoin_context_available": stablecoin_available,
            "stablecoin_context_status": _stablecoin_context_status(stablecoin_evidence, available=stablecoin_available),
            "outcomes": outcomes,
            "classifications": classifications,
            "future_prices_used_in_direction": False,
            "development_same_sample": True,
            "edge_claim_allowed": False,
            "spot_microstructure_independent_origin": False,
            "production_two_origin_gate_changed": False,
            "options_contract_data_used": False,
            "options_profitability_evaluated": False,
            "futures_trade_generated": False,
        })
        await _progress(progress_callback, index + 1, total, "PROCESSING_DIRECTION_CLICKS_V2")

    base_summary = _summary(results)
    base_summary.update({
        "development_same_sample": True,
        "edge_claim_allowed": False,
        "v1_comparison_window_preserved": True,
    })
    return {
        "mode": MODE,
        "version": VERSION,
        "status": "COMPLETED",
        "window_start": start.isoformat(),
        "window_end_exclusive": DEVELOPMENT_WINDOW_END_EXCLUSIVE.isoformat(),
        "last_click_at": end.isoformat(),
        "click_interval_minutes": CLICK_STEP_MINUTES,
        "scheduled_clicks": total,
        "summary": base_summary,
        "v2_diagnostics": _v2_diagnostics(results, oi_rows=oi_rows),
        "coverage": {
            "pit_rows_frozen_at_start": len(frozen_pit_rows),
            "spot_1h_candles": len(one_hour),
            "spot_1h_prefetch_hours": structure_prefetch_hours,
            "spot_1m_candles": len(one_minute),
            "spot_1m_prefetch_minutes": ONE_MINUTE_PREFETCH_MINUTES,
            "delta_oi_5m_candles": len(oi_rows),
            "delta_oi_load_error": oi_load_error,
        },
        "methodology": {
            "primary_question": "NEXT_15M_BTC_DIRECTION",
            "diagnostic_horizons_minutes": list(DIAGNOSTIC_HORIZONS_MINUTES),
            "exact_v1_development_window_reused": True,
            "development_same_sample": True,
            "edge_claim_allowed": False,
            "broad_spot_regime_preserved": True,
            "completed_1m_timing_required_for_spot_direction": True,
            "fresh_intraday_timing_may_veto_broad_spot_regime": True,
            "spot_regime_and_timing_share_one_causal_origin": True,
            "spot_microstructure_may_count_as_independent_confirmation": False,
            "delta_oi_is_separate_leveraged_positioning_origin": True,
            "missing_or_stale_lane_is_negative_vote": False,
            "research_lean_may_use_one_valid_directional_origin": True,
            "production_two_origin_gate_preserved": True,
            "context_only_evidence_may_create_direction": False,
            "outcome_direction_rule": "SIGN_OF_TERMINAL_RETURN_WITH_EXACT_ZERO_FLAT",
            "completed_price_candles_only": True,
            "historical_memory_outcomes_known_by_decision_only": True,
            "future_outcome_used_for_direction": False,
            "retuned_after_v2_outcomes": False,
        },
        "limitations": [
            "V2 rules were designed after inspecting V1 errors on this same 282-click window, so this run is development evidence only and cannot establish edge.",
            "Only point-in-time reconstructible or genuinely archived evidence is admitted; unavailable historical lanes remain missing.",
            "CoinDCX 1-minute microstructure and the broader spot regime are the same price/volume causal origin and never count twice.",
            "Delta OI may provide a separate leveraged-positioning origin but Futures trade generation remains disabled.",
        ],
        "safety": {
            "research_only": True,
            "live_execution": False,
            "capital_committed_inr": 0,
            "options_trade_generated": False,
            "futures_trade_generated": False,
            "production_two_origin_gate_changed": False,
        },
        "clicks": results,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "BTC_CONTINUOUS_PIT_DIRECTION_REPLAY_CONTRACT_V2",
        "frozen_development_clicks": EXPECTED_DEVELOPMENT_CLICKS,
        "click_interval_minutes": CLICK_STEP_MINUTES,
        "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "development_same_sample": True,
        "edge_claim_allowed": False,
        "completed_1m_intraday_timing": True,
        "spot_regime_timing_same_causal_origin": True,
        "spot_microstructure_independent_confirmation": False,
        "delta_oi_independent_positioning_origin": True,
        "production_two_origin_gate_changed": False,
        "future_information_in_direction": False,
        "options_profitability_evaluated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed_inr": 0,
        "research_only": True,
    }
