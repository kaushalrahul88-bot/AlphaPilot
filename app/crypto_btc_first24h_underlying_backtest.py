"""Underlying-only replay for the first shared 24-hour BTC capture window.

Unlike the legacy terminal-return proof, this replay freezes a structural setup
at each 15-minute click and follows entry/invalidation/targets through the
remaining dataset window. It does not select or value an option contract.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
import inspect
from math import ceil
from typing import Any, Awaitable, Callable

from app.coindcx_btc_public_provider import CoinDcxBtcProviderPolicy, CoinDcxBtcPublicProvider
from app.crypto_btc_first24h_backtest import (
    _CachedCoinDcx,
    _fetch_spot,
    _load_shared_window_start,
    frozen_clicks,
)
from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_btc_prospective_proof_bridge import (
    ProspectiveBtcProofBridgePolicy,
    freeze_prospective_btc_thesis_from_existing_sources,
)
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig
from app.crypto_btc_underlying_setup import build_btc_underlying_setup, resolve_btc_underlying_setup
from app.delta_india_btc_derivatives_context import (
    DeltaIndiaBtcDerivativesContextPolicy,
    DeltaIndiaBtcDerivativesPublicProvider,
)

MODE = "BTC_FIRST_SHARED_24H_15M_UNDERLYING_SETUP_REPLAY_V2"


def _market_state_from_frozen(frozen: dict | None) -> dict:
    decision = {} if frozen is None else dict(frozen.get("decision") or {})
    return {
        "instrument_neutral": True,
        "direction": str(decision.get("market_direction") or "UNKNOWN").upper(),
        "state": str(decision.get("market_state") or "NO_FROZEN_DIRECTION"),
    }


def _structure_prefetch_hours(policy: ProspectiveBtcProofBridgePolicy) -> int:
    """Minimum 1h cache span required by the prospective proof bridge.

    The bridge requests the larger of structural history and Historical Memory
    lookback plus a 24h feature anchor, then adds two hours of boundary slack.
    The first replay click occurs at the shared window start, so its cache must
    already contain that entire reconstructible history.
    """
    policy = policy.validated()
    bridge_fetch_hours = max(
        float(policy.structure_lookback_hours),
        float(policy.historical_memory_lookback_hours) + 24.0,
    )
    return max(32, int(ceil(bridge_fetch_hours + 2.0)))


def _summary(clicks: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = Counter(str(row["setup"]["decision"]) for row in clicks)
    outcomes = Counter(str(row["outcome"]["status"]) for row in clicks)
    resolved = [row for row in clicks if row["outcome"]["status"] in {"T1", "T2", "STOP"}]
    wins = sum(row["outcome"]["status"] in {"T1", "T2"} for row in resolved)
    r_values = [float(row["outcome"]["r_multiple"]) for row in resolved]
    derivatives = Counter(str(row.get("derivatives_evidence_status") or "MISSING") for row in clicks)
    memory_available = sum(row.get("historical_memory_available") is True for row in clicks)
    return {
        "decisions": dict(sorted(decisions.items())),
        "outcomes": dict(sorted(outcomes.items())),
        "directional_setups": sum(value for key, value in decisions.items() if key in {"BULLISH", "BEARISH"}),
        "resolved_setups": len(resolved),
        "target_hits": wins,
        "stops": len(resolved) - wins,
        "setup_win_rate_pct": None if not resolved else round(wins / len(resolved) * 100.0, 2),
        "total_r": None if not r_values else round(sum(r_values), 4),
        "average_r": None if not r_values else round(sum(r_values) / len(r_values), 4),
        "derivatives_evidence_status_counts": dict(sorted(derivatives.items())),
        "historical_memory_available_clicks": int(memory_available),
        "historical_memory_missing_clicks": int(len(clicks) - memory_available),
        "options_profitability_evaluated": False,
    }


ProgressCallback = Callable[[int, int, str], Awaitable[None] | None]


async def _progress(callback: ProgressCallback | None, completed: int, total: int, phase: str) -> None:
    if callback is None:
        return
    result = callback(completed, total, phase)
    if inspect.isawaitable(result):
        await result


async def run_first24h_underlying_15m(
    database_url: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    await _progress(progress_callback, 0, 96, "LOADING_ARCHIVED_INPUTS")
    # This replay is underlying-only. Loading thousands of full Delta option
    # snapshot payloads here was unnecessary and could exhaust a 512 MB worker.
    start = await asyncio.to_thread(_load_shared_window_start, database_url)
    clicks = frozen_clicks(start)
    window_end = start + timedelta(hours=24)
    bridge_policy = ProspectiveBtcProofBridgePolicy().validated()
    structure_prefetch_hours = _structure_prefetch_hours(bridge_policy)
    public = CoinDcxBtcPublicProvider(CoinDcxBtcProviderPolicy(enabled=True, timeout_seconds=25))
    one_hour, one_minute, fifteen_minute = await asyncio.gather(
        asyncio.to_thread(
            _fetch_spot,
            public,
            interval="1h",
            start=start - timedelta(hours=structure_prefetch_hours),
            end=window_end,
        ),
        asyncio.to_thread(_fetch_spot, public, interval="1m", start=start-timedelta(minutes=15), end=window_end),
        asyncio.to_thread(_fetch_spot, public, interval="15m", start=start-timedelta(hours=3), end=window_end),
    )
    oi_provider = DeltaIndiaBtcDerivativesPublicProvider(
        DeltaIndiaBtcDerivativesContextPolicy(enabled=True, timeout_seconds=25, resolution="5m")
    )
    oi_rows = await asyncio.to_thread(
        oi_provider.fetch_oi_candles,
        start_at=start-timedelta(hours=2, minutes=15),
        end_at=window_end,
        resolution="5m",
    )
    await _progress(progress_callback, 0, len(clicks), "PROCESSING_CLICKS")
    cached = _CachedCoinDcx({"1h": one_hour, "1m": one_minute})
    pit = PostgresBtcPitArchiveStore(database_url)
    # This policy is required by the existing immutable decision-tape contract.
    # Its four-hour outcome is never resolved or used by this setup replay.
    tape_policy = BtcProspectiveProofRuntimeConfig(evaluation_horizon_hours=4).tape_policy()
    results: list[dict[str, Any]] = []
    for index, click in enumerate(clicks):
        proof = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id=f"first24h-underlying-15m-{index:02d}",
            decision_at=click,
            provider=cached,
            pit_store=pit,
            tape_policy=tape_policy,
            bridge_policy=bridge_policy,
            delta_oi_rows=oi_rows,
        )
        frozen = proof.get("frozen_thesis")
        visible = [row for row in fifteen_minute if row.available_at <= click]
        future = [row for row in fifteen_minute if click < row.available_at <= window_end]
        setup = build_btc_underlying_setup(
            click_id=f"first24h-underlying-15m-{index:02d}",
            decision_at=click,
            market_state=_market_state_from_frozen(frozen),
            completed_candles=visible,
            valid_until=window_end,
        )
        outcome = resolve_btc_underlying_setup(setup=setup, future_candles=future)
        results.append({
            "click_index": index,
            "decision_at": click.isoformat(),
            "proof_status": proof.get("status"),
            "proof_reason": proof.get("reason"),
            "available_lanes": [] if frozen is None else list((frozen.get("decision") or {}).get("available_lanes") or []),
            "missing_lanes": [] if frozen is None else list((frozen.get("decision") or {}).get("missing_lanes") or []),
            "derivatives_evidence_status": proof.get("derivatives_evidence_status"),
            "pit_derivatives_evidence_status": proof.get("pit_derivatives_evidence_status"),
            "delta_oi_evidence_status": proof.get("delta_oi_evidence_status"),
            "historical_memory_available": proof.get("historical_memory_available"),
            "historical_memory_analogue_count": proof.get("historical_memory_analogue_count"),
            "historical_memory_mean_similarity": proof.get("historical_memory_mean_similarity"),
            "historical_memory_median_forward_return_pct": proof.get("historical_memory_median_forward_return_pct"),
            "options_context_available": proof.get("options_context_available"),
            "stablecoin_context_available": proof.get("stablecoin_context_available"),
            "setup": setup,
            "outcome": outcome,
        })
        await _progress(progress_callback, index + 1, len(clicks), "PROCESSING_CLICKS")
    return {
        "mode": MODE,
        "status": "COMPLETED",
        "window_start": start.isoformat(),
        "window_end_exclusive": window_end.isoformat(),
        "click_interval_minutes": 15,
        "scheduled_clicks": len(clicks),
        "summary": _summary(results),
        "coverage": {
            "spot_1h_candles": len(one_hour),
            "spot_1h_prefetch_hours": structure_prefetch_hours,
            "historical_memory_lookback_hours": int(bridge_policy.historical_memory_lookback_hours),
            "spot_1m_candles": len(one_minute),
            "spot_15m_candles": len(fifteen_minute),
            "delta_oi_5m_candles": len(oi_rows),
        },
        "methodology": {
            "underlying_direction_test_only": True,
            "fixed_terminal_return_horizon_used": False,
            "setup_valid_until": "FIRST_24H_DATASET_END",
            "completed_candles_only": True,
            "historical_memory_reconstructed_from_completed_coindcx_1h": True,
            "historical_memory_direction_creator": False,
            "same_candle_ordering": "AMBIGUOUS",
            "minimum_target1_r": 1.5,
            "retuned_after_outcomes": False,
        },
        "limitations": [
            "The original first-day archive can use only irrecoverable evidence genuinely visible during that window.",
            "Reconstructible completed CoinDCX price history may be loaded before that window when its bar-completion availability is proven.",
            "Evidence collectors activated after this window are not backfilled into historical clicks.",
            "Options profitability is intentionally not evaluated in this underlying-direction test.",
        ],
        "safety": {
            "research_only": True,
            "live_execution": False,
            "capital_committed_inr": 0,
            "futures_context_only": True,
            "futures_trade_generated": False,
            "options_trade_generated": False,
        },
        "clicks": results,
    }
