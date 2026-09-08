"""Continuous point-in-time BTC direction replay at 15-minute clicks.

This is a research experiment, not a trading route.  It freezes an UP/DOWN/WAIT
lean from evidence genuinely visible at each historical click, then scores that
lean against later completed CoinDCX BTC spot candles.  Missing evidence lanes
are neutral rather than negative.  The production two-origin trade-quality gate
is preserved and reported alongside the separate research lean.

No option contract, premium, P&L, Futures trade, order, or capital is created.
"""
from __future__ import annotations

import asyncio
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
import inspect
from math import ceil
from typing import Any, Awaitable, Callable

from app.coindcx_btc_public_provider import CoinDcxBtcProviderPolicy, CoinDcxBtcPublicProvider
from app.crypto_btc_derivatives_evidence import derivatives_evidence_from_full_pit_context
from app.crypto_btc_first24h_backtest import _CachedCoinDcx, _fetch_spot
from app.crypto_btc_first24h_underlying_backtest import _structure_prefetch_hours
from app.crypto_btc_historical_analogue import (
    BtcHistoricalAnaloguePolicy,
    derive_btc_historical_analogue_evidence,
)
from app.crypto_btc_historical_data_adapter import (
    BtcHistoricalArchive,
    derive_spot_structure_evidence,
)
from app.crypto_btc_information_board import build_btc_information_board
from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore, TABLE_NAME as PIT_TABLE
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
MODE = "BTC_CONTINUOUS_PIT_15M_DIRECTION_REPLAY_V1"
CLICK_STEP_MINUTES = 15
PRIMARY_HORIZON_MINUTES = 15
DIAGNOSTIC_HORIZONS_MINUTES = (15, 30, 60)
MAX_TERMINAL_GAP_SECONDS = 120

ProgressCallback = Callable[[int, int, str], Awaitable[None] | None]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _ceil_quarter_hour(value: datetime) -> datetime:
    value = _utc(value).replace(second=0, microsecond=0)
    remainder = value.minute % CLICK_STEP_MINUTES
    if remainder == 0:
        return value
    return value + timedelta(minutes=CLICK_STEP_MINUTES - remainder)


def _floor_quarter_hour(value: datetime) -> datetime:
    value = _utc(value).replace(second=0, microsecond=0)
    return value - timedelta(minutes=value.minute % CLICK_STEP_MINUTES)


def _click_grid(first_seen: datetime, last_seen: datetime) -> list[datetime]:
    start = _ceil_quarter_hour(first_seen)
    end = _floor_quarter_hour(last_seen)
    if end < start:
        return []
    count = int((end - start).total_seconds() // (CLICK_STEP_MINUTES * 60)) + 1
    return [start + timedelta(minutes=CLICK_STEP_MINUTES * index) for index in range(count)]


def _load_pit_bounds_sync(database_url: str) -> dict[str, Any]:
    with _connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT MIN(first_seen_at), MAX(first_seen_at), COUNT(*)::BIGINT FROM {PIT_TABLE}"
        )
        first_seen, last_seen, total_rows = cur.fetchone()
        cur.execute(
            f"SELECT dataset, COUNT(*)::BIGINT, MIN(first_seen_at), MAX(first_seen_at) "
            f"FROM {PIT_TABLE} GROUP BY dataset ORDER BY dataset"
        )
        datasets = [
            {
                "dataset": str(dataset),
                "rows": int(rows),
                "first_seen_at": _utc(first_at).isoformat(),
                "last_seen_at": _utc(last_at).isoformat(),
            }
            for dataset, rows, first_at, last_at in cur.fetchall()
        ]
        cur.execute(
            f"WITH ordered AS ("
            f" SELECT first_seen_at, LAG(first_seen_at) OVER (ORDER BY first_seen_at) AS prev"
            f" FROM {PIT_TABLE} WHERE dataset='BTC_FUTURES_FUNDING_MARK_SNAPSHOT'"
            f") SELECT COALESCE(MAX(EXTRACT(EPOCH FROM (first_seen_at-prev))),0),"
            f" COUNT(*) FILTER (WHERE first_seen_at-prev > INTERVAL '15 minutes')"
            f" FROM ordered WHERE prev IS NOT NULL"
        )
        max_gap_seconds, gaps_gt_15m = cur.fetchone()

    if first_seen is None or last_seen is None:
        return {
            "ready": False,
            "reason": "BTC_PIT_ARCHIVE_EMPTY",
            "total_pit_rows": 0,
            "datasets": [],
            "scheduled_clicks": 0,
        }
    clicks = _click_grid(_utc(first_seen), _utc(last_seen))
    return {
        "ready": bool(clicks),
        "reason": None if clicks else "BTC_PIT_WINDOW_TOO_SHORT_FOR_15M_GRID",
        "first_pit_seen_at": _utc(first_seen).isoformat(),
        "last_pit_seen_at": _utc(last_seen).isoformat(),
        "window_start": None if not clicks else clicks[0].isoformat(),
        "window_end_inclusive": None if not clicks else clicks[-1].isoformat(),
        "scheduled_clicks": len(clicks),
        "click_interval_minutes": CLICK_STEP_MINUTES,
        "total_pit_rows": int(total_rows),
        "datasets": datasets,
        "funding_mark_max_gap_seconds": float(max_gap_seconds or 0.0),
        "funding_mark_gaps_gt_15m": int(gaps_gt_15m or 0),
        "gaps_remove_clicks": False,
        "missing_or_stale_lanes_are_neutral": True,
    }


async def continuous_direction_readiness(database_url: str) -> dict[str, Any]:
    result = await asyncio.to_thread(_load_pit_bounds_sync, database_url)
    return {
        "version": "BTC_CONTINUOUS_DIRECTION_READINESS_V1",
        "mode": MODE,
        **result,
        "research_only": True,
        "options_profitability_evaluated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed_inr": 0,
    }


async def _progress(
    callback: ProgressCallback | None,
    completed: int,
    total: int,
    phase: str,
) -> None:
    if callback is None:
        return
    result = callback(completed, total, phase)
    if inspect.isawaitable(result):
        await result


class _FrozenPitRows:
    """Bounded in-memory PIT view frozen at replay start."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        ordered = sorted(rows, key=lambda row: str(row.get("first_seen_at") or ""))
        self.rows = ordered
        self.times = [
            _utc(datetime.fromisoformat(str(row["first_seen_at"]).replace("Z", "+00:00")))
            for row in ordered
        ]

    def visible_as_of(self, decision_at: datetime) -> list[dict[str, Any]]:
        cutoff = bisect_right(self.times, _utc(decision_at))
        return self.rows[:cutoff]


def _terminal_outcome(
    rows: list[Any],
    *,
    decision_at: datetime,
    entry_price: float,
    horizon_minutes: int,
) -> dict[str, Any]:
    due = _utc(decision_at) + timedelta(minutes=int(horizon_minutes))
    candidates = [
        row
        for row in rows
        if _utc(decision_at) < _utc(row.available_at) <= due
    ]
    if not candidates:
        return {
            "status": "UNRESOLVED",
            "reason": "NO_COMPLETED_BTC_PRICE_BY_HORIZON",
            "horizon_minutes": int(horizon_minutes),
        }
    terminal = max(candidates, key=lambda row: _utc(row.available_at))
    gap = (due - _utc(terminal.available_at)).total_seconds()
    if gap < 0 or gap > MAX_TERMINAL_GAP_SECONDS:
        return {
            "status": "UNRESOLVED",
            "reason": "TERMINAL_BTC_PRICE_TOO_FAR_FROM_HORIZON",
            "horizon_minutes": int(horizon_minutes),
            "terminal_gap_seconds": gap,
        }
    terminal_price = float(terminal.close)
    return_pct = (terminal_price - float(entry_price)) / float(entry_price) * 100.0
    realized = "UP" if return_pct > 0 else "DOWN" if return_pct < 0 else "FLAT"
    return {
        "status": "RESOLVED",
        "horizon_minutes": int(horizon_minutes),
        "due_at": due.isoformat(),
        "terminal_at": _utc(terminal.available_at).isoformat(),
        "terminal_gap_seconds": gap,
        "entry_btc_price": float(entry_price),
        "terminal_btc_price": terminal_price,
        "terminal_return_pct": return_pct,
        "realized_direction": realized,
    }


def _classification(prediction: str, outcome: dict[str, Any]) -> str:
    if outcome.get("status") != "RESOLVED":
        return "UNRESOLVED"
    realized = str(outcome.get("realized_direction") or "FLAT")
    if prediction == "WAIT":
        return "ABSTENTION"
    if realized == "FLAT":
        return "FLAT"
    return "HIT" if prediction == realized else "MISS"


def _horizon_summary(clicks: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    key = str(int(horizon))
    resolved = [row for row in clicks if (row["outcomes"].get(key) or {}).get("status") == "RESOLVED"]
    directional = [row for row in resolved if row["research_direction"] in {"UP", "DOWN"}]
    scorable = [
        row for row in directional
        if str((row["outcomes"].get(key) or {}).get("realized_direction")) in {"UP", "DOWN"}
    ]
    hits = sum(row["classifications"].get(key) == "HIT" for row in scorable)
    return {
        "horizon_minutes": int(horizon),
        "resolved_outcomes": len(resolved),
        "directional_predictions": len(directional),
        "scorable_directional_predictions": len(scorable),
        "hits": hits,
        "misses": len(scorable) - hits,
        "directional_accuracy_pct": None if not scorable else round(hits / len(scorable) * 100.0, 2),
    }


def _summary(clicks: list[dict[str, Any]]) -> dict[str, Any]:
    predictions = Counter(row["research_direction"] for row in clicks)
    production = Counter(row["production_direction"] for row in clicks)
    primary_key = str(PRIMARY_HORIZON_MINUTES)
    primary_resolved = [
        row for row in clicks if (row["outcomes"].get(primary_key) or {}).get("status") == "RESOLVED"
    ]
    primary_directional = [row for row in primary_resolved if row["research_direction"] in {"UP", "DOWN"}]
    primary_scorable = [
        row for row in primary_directional
        if str((row["outcomes"].get(primary_key) or {}).get("realized_direction")) in {"UP", "DOWN"}
    ]
    hits = sum(row["classifications"].get(primary_key) == "HIT" for row in primary_scorable)
    waits = [row for row in primary_resolved if row["research_direction"] == "WAIT"]
    actuals = Counter(
        str((row["outcomes"].get(primary_key) or {}).get("realized_direction") or "UNKNOWN")
        for row in primary_resolved
    )
    confidence = Counter(row["research_confidence"] for row in clicks)
    return {
        "predictions": dict(sorted(predictions.items())),
        "production_market_directions": dict(sorted(production.items())),
        "research_directional_predictions": len(primary_directional),
        "research_coverage_pct": None if not primary_resolved else round(len(primary_directional) / len(primary_resolved) * 100.0, 2),
        "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "primary_resolved_outcomes": len(primary_resolved),
        "primary_scorable_directional_predictions": len(primary_scorable),
        "primary_hits": hits,
        "primary_misses": len(primary_scorable) - hits,
        "primary_directional_accuracy_pct": None if not primary_scorable else round(hits / len(primary_scorable) * 100.0, 2),
        "waits": len(waits),
        "wait_followed_up": sum((row["outcomes"].get(primary_key) or {}).get("realized_direction") == "UP" for row in waits),
        "wait_followed_down": sum((row["outcomes"].get(primary_key) or {}).get("realized_direction") == "DOWN" for row in waits),
        "actual_primary_directions": dict(sorted(actuals.items())),
        "research_confidence_bands": dict(sorted(confidence.items())),
        "horizons": {
            str(horizon): _horizon_summary(clicks, horizon)
            for horizon in DIAGNOSTIC_HORIZONS_MINUTES
        },
        "options_profitability_evaluated": False,
        "production_trade_gate_changed": False,
    }


async def run_continuous_direction_15m(
    database_url: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    readiness = await continuous_direction_readiness(database_url)
    if readiness.get("ready") is not True:
        raise ValueError(str(readiness.get("reason") or "BTC continuous direction replay is not ready"))
    start = _utc(datetime.fromisoformat(str(readiness["window_start"]).replace("Z", "+00:00")))
    end = _utc(datetime.fromisoformat(str(readiness["window_end_inclusive"]).replace("Z", "+00:00")))
    clicks = _click_grid(start, end)
    total = len(clicks)
    await _progress(progress_callback, 0, total, "LOADING_FROZEN_INPUTS")

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
            start=start - timedelta(minutes=15),
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
        # External reconstructible OI is enrichment, not a reason to fabricate or
        # abort.  PIT derivatives and all other available lanes remain usable.
        oi_load_error = f"{exc.__class__.__name__}: {str(exc)[:240]}"
        oi_rows = []

    await _progress(progress_callback, 0, total, "PROCESSING_DIRECTION_CLICKS")
    results: list[dict[str, Any]] = []
    for index, click in enumerate(clicks):
        visible_structure = _visible_completed(one_hour, as_of=click)
        decision_rows = cached.fetch_spot_candles(
            interval="1m",
            start_at=click - timedelta(minutes=10),
            end_at=click,
            limit=20,
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
                "available_lanes": [],
                "missing_lanes": [],
                "outcomes": {},
                "classifications": {},
            })
            await _progress(progress_callback, index + 1, total, "PROCESSING_DIRECTION_CLICKS")
            continue

        archive = BtcHistoricalArchive(spot_candles=tuple(visible_structure)).validated()
        spot_evidence = derive_spot_structure_evidence(
            archive,
            decision_at=click,
            max_spot_age_seconds=int(bridge_policy.structure_max_age_seconds),
        )
        evidence = [] if spot_evidence is None else [spot_evidence]

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

        board = build_btc_information_board(
            evidence,
            decision_at=click,
            trade_horizon="intraday",
        )
        lean = derive_btc_research_directional_lean(
            evidence,
            decision_at=click,
            trade_horizon="intraday",
        )
        production_state = board.get("underlying_market_state") or {}
        production_direction = str(production_state.get("direction") or "UNKNOWN").upper()

        outcomes = {
            str(horizon): _terminal_outcome(
                one_minute,
                decision_at=click,
                entry_price=float(latest.close),
                horizon_minutes=horizon,
            )
            for horizon in DIAGNOSTIC_HORIZONS_MINUTES
        }
        prediction = str(lean["direction"])
        classifications = {
            key: _classification(prediction, outcome)
            for key, outcome in outcomes.items()
        }
        lane_status = board.get("lane_status") or {}
        available_lanes = sorted(
            lane for lane, row in lane_status.items() if row.get("available") is True
        )
        results.append({
            "click_index": index,
            "decision_at": click.isoformat(),
            "decision_status": "FROZEN_RESEARCH_DIRECTION",
            "decision_btc_price": float(latest.close),
            "research_direction": prediction,
            "research_confidence": str(lean["confidence_band"]),
            "research_directional_lean": lean,
            "production_direction": production_direction,
            "production_market_state": production_state.get("state"),
            "available_lanes": available_lanes,
            "missing_lanes": sorted(board.get("missing_lanes") or []),
            "pit_record_count_visible": len(pit_rows),
            "derivatives_evidence_status": None if derivatives is None else derivatives.stance,
            "pit_derivatives_evidence_status": None if pit_derivatives is None else pit_derivatives.stance,
            "delta_oi_evidence_status": None if delta_derivatives is None else delta_derivatives.stance,
            "historical_memory_available": memory_evidence is not None,
            "historical_memory_analogue_count": None if memory_evidence is None else memory_evidence.metadata.get("analogue_count"),
            "historical_memory_median_forward_return_pct": None if memory_evidence is None else memory_evidence.metadata.get("median_forward_return_pct"),
            "options_context_available": options_available,
            "stablecoin_context_available": stablecoin_available,
            "stablecoin_context_status": _stablecoin_context_status(stablecoin_evidence, available=stablecoin_available),
            "outcomes": outcomes,
            "classifications": classifications,
            "future_prices_used_in_direction": False,
            "options_contract_data_used": False,
            "options_profitability_evaluated": False,
            "futures_trade_generated": False,
        })
        await _progress(progress_callback, index + 1, total, "PROCESSING_DIRECTION_CLICKS")

    return {
        "mode": MODE,
        "status": "COMPLETED",
        "window_start": start.isoformat(),
        "window_end_exclusive": (end + timedelta(minutes=CLICK_STEP_MINUTES)).isoformat(),
        "last_click_at": end.isoformat(),
        "click_interval_minutes": CLICK_STEP_MINUTES,
        "scheduled_clicks": total,
        "summary": _summary(results),
        "coverage": {
            "pit_rows_frozen_at_start": len(frozen_pit_rows),
            "pit_datasets": readiness.get("datasets"),
            "funding_mark_max_gap_seconds": readiness.get("funding_mark_max_gap_seconds"),
            "funding_mark_gaps_gt_15m": readiness.get("funding_mark_gaps_gt_15m"),
            "spot_1h_candles": len(one_hour),
            "spot_1h_prefetch_hours": structure_prefetch_hours,
            "spot_1m_candles": len(one_minute),
            "delta_oi_5m_candles": len(oi_rows),
            "delta_oi_load_error": oi_load_error,
        },
        "methodology": {
            "primary_question": "NEXT_15M_BTC_DIRECTION",
            "diagnostic_horizons_minutes": list(DIAGNOSTIC_HORIZONS_MINUTES),
            "click_grid_uses_all_available_pit_span": True,
            "clicks_removed_for_pit_gaps": False,
            "missing_or_stale_lane_is_negative_vote": False,
            "research_lean_may_use_one_valid_directional_origin": True,
            "production_two_origin_gate_preserved": True,
            "context_only_evidence_may_create_direction": False,
            "outcome_direction_rule": "SIGN_OF_TERMINAL_RETURN_WITH_EXACT_ZERO_FLAT",
            "completed_price_candles_only": True,
            "historical_memory_outcomes_known_by_decision_only": True,
            "future_outcome_used_for_direction": False,
            "retuned_after_outcomes": False,
        },
        "limitations": [
            "Only evidence that can be reconstructed point-in-time or was genuinely archived by the click is admitted.",
            "News, social, on-chain, or macro lanes that lack point-in-time evidence for a historical click remain missing rather than being backfilled from future knowledge.",
            "Historical CoinDCX candles and completed Delta OI candles are reconstructible market history; PIT archive rows remain distinct first-seen evidence.",
            "Options-market evidence, when available, is context only and options profitability is intentionally excluded.",
        ],
        "safety": {
            "research_only": True,
            "live_execution": False,
            "capital_committed_inr": 0,
            "options_trade_generated": False,
            "futures_trade_generated": False,
        },
        "clicks": results,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "BTC_CONTINUOUS_PIT_DIRECTION_REPLAY_CONTRACT_V1",
        "click_interval_minutes": CLICK_STEP_MINUTES,
        "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "uses_all_available_pit_span": True,
        "pit_gaps_fabricated_as_continuity": False,
        "missing_lanes_are_negative_votes": False,
        "production_two_origin_gate_changed": False,
        "research_single_origin_lean_allowed": True,
        "future_information_in_direction": False,
        "options_contract_required": False,
        "options_profitability_evaluated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "research_only": True,
    }
