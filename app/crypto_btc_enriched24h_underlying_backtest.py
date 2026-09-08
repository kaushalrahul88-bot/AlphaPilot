"""Readiness-gated 24-hour BTC replay using prospectively captured context.

The original first-day replay is intentionally immutable. This module selects a
*later* 24-hour window only after the newer point-in-time collectors have enough
history to make their context meaningful at every 15-minute click:

- Deribit BTC global options chain context is fresh and has at least 20 prior IV
  observations, so the point-in-time IV percentile is defined.
- DefiLlama aggregate stablecoin supply is fresh and has a genuinely prior
  24-hour comparison observation, so the liquidity state is defined.

Historical Memory is reconstructed only from completed CoinDCX 1h bars and is
still context-only. The replay remains underlying-direction research: it does
not value an option, generate a Futures trade, place an order, or commit capital.
"""
from __future__ import annotations

import asyncio
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
from typing import Any, Awaitable, Callable

from app.coindcx_btc_public_provider import CoinDcxBtcProviderPolicy, CoinDcxBtcPublicProvider
from app.crypto_btc_first24h_backtest import _CachedCoinDcx, _fetch_spot
from app.crypto_btc_first24h_underlying_backtest import _market_state_from_frozen
from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore, TABLE_NAME as PIT_TABLE
from app.crypto_btc_prospective_proof_bridge import (
    ProspectiveBtcProofBridgePolicy,
    freeze_prospective_btc_thesis_from_existing_sources,
)
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig
from app.crypto_btc_underlying_setup import build_btc_underlying_setup, resolve_btc_underlying_setup
from app.crypto_deribit_options_evidence import DeribitOptionsEvidencePolicy
from app.crypto_deribit_options_pit import DATASET as OPTIONS_CONTEXT_DATASET
from app.crypto_stablecoin_liquidity import StablecoinLiquidityPolicy
from app.crypto_stablecoin_pit_capture import STABLECOIN_SUPPLY_DATASET

UTC = timezone.utc
MODE = "BTC_ENRICHED_PIT_24H_15M_UNDERLYING_SETUP_REPLAY_V1"
CLICK_COUNT = 96
CLICK_STEP = timedelta(minutes=15)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else _utc(value).isoformat()


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


@dataclass(frozen=True)
class Enriched24hReadinessPolicy:
    click_count: int = CLICK_COUNT
    click_step_minutes: int = 15
    options_min_prior_samples: int = 20
    options_max_age_seconds: int = 15 * 60
    stablecoin_comparison_hours: int = 24
    stablecoin_max_age_seconds: int = 2 * 60 * 60

    @classmethod
    def from_evidence_policies(cls) -> "Enriched24hReadinessPolicy":
        options = DeribitOptionsEvidencePolicy().validated()
        stable = StablecoinLiquidityPolicy().validated()
        return cls(
            options_min_prior_samples=int(options.min_prior_iv_samples),
            options_max_age_seconds=int(options.max_snapshot_age_seconds),
            stablecoin_comparison_hours=int(stable.comparison_hours),
            stablecoin_max_age_seconds=int(stable.max_snapshot_age_seconds),
        ).validated()

    def validated(self) -> "Enriched24hReadinessPolicy":
        if int(self.click_count) != 96:
            raise ValueError("enriched V1 requires exactly 96 clicks")
        if int(self.click_step_minutes) != 15:
            raise ValueError("enriched V1 requires 15-minute clicks")
        if int(self.options_min_prior_samples) < 2:
            raise ValueError("options_min_prior_samples must be >= 2")
        if int(self.options_max_age_seconds) <= 0:
            raise ValueError("options_max_age_seconds must be > 0")
        if int(self.stablecoin_comparison_hours) < 1:
            raise ValueError("stablecoin_comparison_hours must be >= 1")
        if int(self.stablecoin_max_age_seconds) <= 0:
            raise ValueError("stablecoin_max_age_seconds must be > 0")
        return self


def _load_context_timestamps(database_url: str) -> dict[str, list[datetime]]:
    with _connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT dataset, first_seen_at FROM {PIT_TABLE} "
            "WHERE dataset = ANY(%s) ORDER BY first_seen_at ASC",
            ([OPTIONS_CONTEXT_DATASET, STABLECOIN_SUPPLY_DATASET],),
        )
        rows = cur.fetchall()
    result = {OPTIONS_CONTEXT_DATASET: [], STABLECOIN_SUPPLY_DATASET: []}
    for dataset, first_seen_at in rows:
        if dataset in result and first_seen_at is not None:
            result[dataset].append(_utc(first_seen_at))
    return result


def _latest_at_or_before(rows: list[datetime], at: datetime) -> tuple[int, datetime] | None:
    index = bisect_right(rows, _utc(at)) - 1
    return None if index < 0 else (index, rows[index])


def _options_ready_at(rows: list[datetime], at: datetime, policy: Enriched24hReadinessPolicy) -> bool:
    latest = _latest_at_or_before(rows, at)
    if latest is None:
        return False
    index, seen = latest
    age = (_utc(at) - seen).total_seconds()
    return age <= int(policy.options_max_age_seconds) and index >= int(policy.options_min_prior_samples)


def _stablecoin_ready_at(rows: list[datetime], at: datetime, policy: Enriched24hReadinessPolicy) -> bool:
    latest = _latest_at_or_before(rows, at)
    if latest is None:
        return False
    index, seen = latest
    age = (_utc(at) - seen).total_seconds()
    if age > int(policy.stablecoin_max_age_seconds):
        return False
    target = seen - timedelta(hours=int(policy.stablecoin_comparison_hours))
    return bisect_right(rows[:index], target) > 0


def _candidate_starts(
    options_rows: list[datetime],
    stable_rows: list[datetime],
    *,
    as_of: datetime,
    policy: Enriched24hReadinessPolicy,
) -> list[datetime]:
    cutoff = _utc(as_of)
    return [
        seen for seen in stable_rows
        if seen <= cutoff
        and _stablecoin_ready_at(stable_rows, seen, policy)
        and _options_ready_at(options_rows, seen, policy)
    ]


def evaluate_enriched_24h_readiness(
    timestamps_by_dataset: dict[str, list[datetime]],
    *,
    as_of: datetime,
    policy: Enriched24hReadinessPolicy | None = None,
) -> dict[str, Any]:
    """Choose the earliest scientifically usable contiguous enriched 24h window."""
    policy = (policy or Enriched24hReadinessPolicy.from_evidence_policies()).validated()
    observed_at = _utc(as_of)
    options_rows = sorted(_utc(row) for row in timestamps_by_dataset.get(OPTIONS_CONTEXT_DATASET, []))
    stable_rows = sorted(_utc(row) for row in timestamps_by_dataset.get(STABLECOIN_SUPPLY_DATASET, []))

    first_options = options_rows[0] if options_rows else None
    first_stable = stable_rows[0] if stable_rows else None
    options_history_ready = (
        options_rows[int(policy.options_min_prior_samples)]
        if len(options_rows) > int(policy.options_min_prior_samples)
        else None
    )
    theoretical_stable_ready = (
        first_stable + timedelta(hours=int(policy.stablecoin_comparison_hours))
        if first_stable is not None else None
    )
    theoretical_start_candidates = [row for row in (options_history_ready, theoretical_stable_ready) if row is not None]
    theoretical_start = max(theoretical_start_candidates) if len(theoretical_start_candidates) == 2 else None
    theoretical_complete = None if theoretical_start is None else theoretical_start + timedelta(hours=24)

    candidates = _candidate_starts(
        options_rows,
        stable_rows,
        as_of=observed_at,
        policy=policy,
    )
    best: dict[str, Any] | None = None
    for start in candidates:
        valid_clicks = 0
        first_invalid_reason: str | None = None
        for index in range(int(policy.click_count)):
            click = start + index * timedelta(minutes=int(policy.click_step_minutes))
            if click > observed_at:
                first_invalid_reason = "WINDOW_STILL_COLLECTING"
                break
            if not _options_ready_at(options_rows, click, policy):
                first_invalid_reason = "OPTIONS_CONTEXT_MISSING_OR_STALE"
                break
            if not _stablecoin_ready_at(stable_rows, click, policy):
                first_invalid_reason = "STABLECOIN_CONTEXT_MISSING_STALE_OR_NO_24H_PRIOR"
                break
            valid_clicks += 1

        window_end = start + timedelta(hours=24)
        ready = valid_clicks == int(policy.click_count) and window_end <= observed_at
        candidate = {
            "window_start": start,
            "window_end_exclusive": window_end,
            "covered_clicks": valid_clicks,
            "ready": ready,
            "first_invalid_reason": None if ready else first_invalid_reason or "WINDOW_OUTCOME_HORIZON_STILL_COLLECTING",
        }
        if best is None:
            best = candidate
        elif bool(candidate["ready"]) and not bool(best["ready"]):
            best = candidate
        elif bool(candidate["ready"]) == bool(best["ready"]):
            if int(candidate["covered_clicks"]) > int(best["covered_clicks"]):
                best = candidate
            elif int(candidate["covered_clicks"]) == int(best["covered_clicks"]) and candidate["window_start"] < best["window_start"]:
                best = candidate

    if best is None:
        if not options_rows:
            state = "WAITING_FOR_OPTIONS_CONTEXT"
            next_requirement = "Wait for the first Deribit BTC options PIT snapshot."
        elif len(options_rows) <= int(policy.options_min_prior_samples):
            state = "BUILDING_OPTIONS_IV_HISTORY"
            next_requirement = f"Collect at least {int(policy.options_min_prior_samples) + 1} Deribit options snapshots."
        elif not stable_rows:
            state = "WAITING_FOR_STABLECOIN_CONTEXT"
            next_requirement = "Wait for the first stablecoin-liquidity PIT snapshot."
        else:
            state = "BUILDING_STABLECOIN_COMPARISON_HISTORY"
            next_requirement = f"Build a genuine {int(policy.stablecoin_comparison_hours)}-hour first-seen stablecoin comparison before starting the enriched window."
        window_start = None
        window_end = None
        covered_clicks = 0
        invalid_reason = state
        ready = False
    else:
        window_start = best["window_start"]
        window_end = best["window_end_exclusive"]
        covered_clicks = int(best["covered_clicks"])
        invalid_reason = best["first_invalid_reason"]
        ready = bool(best["ready"])
        state = "READY" if ready else ("COLLECTING_ENRICHED_WINDOW" if invalid_reason == "WINDOW_STILL_COLLECTING" else "CONTEXT_CONTINUITY_NOT_YET_24H")
        next_requirement = (
            "The enriched 24-hour replay can run now."
            if ready else
            "Keep the PIT collectors running until one contiguous 24-hour window has fresh options and stablecoin context at all 96 clicks."
        )

    return {
        "version": "BTC_ENRICHED_24H_READINESS_V1",
        "status": state,
        "ready": ready,
        "checked_at": observed_at.isoformat(),
        "window_start": _iso(window_start),
        "window_end_exclusive": _iso(window_end),
        "covered_clicks": covered_clicks,
        "required_clicks": int(policy.click_count),
        "coverage_pct": round(covered_clicks / int(policy.click_count) * 100.0, 1),
        "next_requirement": next_requirement,
        "first_incomplete_reason": invalid_reason,
        "theoretical_earliest_start": _iso(theoretical_start),
        "theoretical_earliest_complete": _iso(theoretical_complete),
        "theoretical_dates_assume_continuous_collection": True,
        "requirements": {
            "click_interval_minutes": int(policy.click_step_minutes),
            "options_min_prior_iv_samples": int(policy.options_min_prior_samples),
            "options_max_age_seconds": int(policy.options_max_age_seconds),
            "stablecoin_comparison_hours": int(policy.stablecoin_comparison_hours),
            "stablecoin_max_age_seconds": int(policy.stablecoin_max_age_seconds),
            "window_outcomes_must_be_fully_observable": True,
        },
        "datasets": {
            OPTIONS_CONTEXT_DATASET: {
                "count": len(options_rows),
                "first_seen_at": _iso(first_options),
                "latest_seen_at": _iso(options_rows[-1] if options_rows else None),
                "history_ready_at": _iso(options_history_ready),
            },
            STABLECOIN_SUPPLY_DATASET: {
                "count": len(stable_rows),
                "first_seen_at": _iso(first_stable),
                "latest_seen_at": _iso(stable_rows[-1] if stable_rows else None),
                "theoretical_comparison_ready_at": _iso(theoretical_stable_ready),
            },
        },
        "safety": {
            "research_only": True,
            "live_execution": False,
            "capital_committed_inr": 0,
            "options_trade_generated": False,
            "futures_trade_generated": False,
        },
    }


async def enriched_24h_readiness(database_url: str, *, as_of: datetime | None = None) -> dict[str, Any]:
    timestamps = await asyncio.to_thread(_load_context_timestamps, database_url)
    return evaluate_enriched_24h_readiness(
        timestamps,
        as_of=_utc(as_of or datetime.now(UTC)),
    )


ProgressCallback = Callable[[int, int, str], Awaitable[None] | None]


async def _progress(callback: ProgressCallback | None, completed: int, total: int, phase: str) -> None:
    if callback is None:
        return
    result = callback(completed, total, phase)
    if inspect.isawaitable(result):
        await result


def _summary(clicks: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = Counter(str(row["setup"]["decision"]) for row in clicks)
    outcomes = Counter(str(row["outcome"]["status"]) for row in clicks)
    resolved = [row for row in clicks if row["outcome"]["status"] in {"T1", "T2", "STOP"}]
    wins = sum(row["outcome"]["status"] in {"T1", "T2"} for row in resolved)
    r_values = [float(row["outcome"]["r_multiple"]) for row in resolved]
    return {
        "replay_mode": "ENRICHED_PIT_24H",
        "decisions": dict(sorted(decisions.items())),
        "outcomes": dict(sorted(outcomes.items())),
        "directional_setups": sum(value for key, value in decisions.items() if key in {"BULLISH", "BEARISH"}),
        "resolved_setups": len(resolved),
        "target_hits": wins,
        "stops": len(resolved) - wins,
        "setup_win_rate_pct": None if not resolved else round(wins / len(resolved) * 100.0, 2),
        "total_r": None if not r_values else round(sum(r_values), 4),
        "average_r": None if not r_values else round(sum(r_values) / len(r_values), 4),
        "historical_memory_available_clicks": sum(bool(row.get("historical_memory_available")) for row in clicks),
        "options_context_available_clicks": sum(bool(row.get("options_context_available")) for row in clicks),
        "stablecoin_context_available_clicks": sum(bool(row.get("stablecoin_context_available")) for row in clicks),
        "stablecoin_ready_clicks": sum(str(row.get("stablecoin_context_status")) == "READY" for row in clicks),
        "derivatives_evidence_status_counts": dict(sorted(Counter(str(row.get("derivatives_evidence_status") or "NONE") for row in clicks).items())),
        "options_profitability_evaluated": False,
    }


async def run_enriched24h_underlying_15m(
    database_url: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    await _progress(progress_callback, 0, CLICK_COUNT, "CHECKING_ENRICHED_READINESS")
    readiness = await enriched_24h_readiness(database_url)
    if not readiness.get("ready"):
        raise ValueError(
            f"ENRICHED_24H_NOT_READY: {readiness.get('status')} - {readiness.get('next_requirement')}"
        )
    start = _utc(datetime.fromisoformat(str(readiness["window_start"]).replace("Z", "+00:00")))
    window_end = start + timedelta(hours=24)
    clicks = [start + index * CLICK_STEP for index in range(CLICK_COUNT)]

    await _progress(progress_callback, 0, CLICK_COUNT, "LOADING_ENRICHED_INPUTS")
    bridge_policy = ProspectiveBtcProofBridgePolicy().validated()
    prefetch_hours = int(bridge_policy.historical_memory_lookback_hours) + 26
    public = CoinDcxBtcPublicProvider(CoinDcxBtcProviderPolicy(enabled=True, timeout_seconds=25))
    one_hour, one_minute, fifteen_minute = await asyncio.gather(
        asyncio.to_thread(_fetch_spot, public, interval="1h", start=start-timedelta(hours=prefetch_hours), end=window_end),
        asyncio.to_thread(_fetch_spot, public, interval="1m", start=start-timedelta(minutes=15), end=window_end),
        asyncio.to_thread(_fetch_spot, public, interval="15m", start=start-timedelta(hours=3), end=window_end),
    )
    cached = _CachedCoinDcx({"1h": one_hour, "1m": one_minute})
    pit = PostgresBtcPitArchiveStore(database_url)
    tape_policy = BtcProspectiveProofRuntimeConfig(evaluation_horizon_hours=4).tape_policy()

    await _progress(progress_callback, 0, CLICK_COUNT, "PROCESSING_ENRICHED_CLICKS")
    results: list[dict[str, Any]] = []
    for index, click in enumerate(clicks):
        proof = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id=f"enriched24h-underlying-15m-{index:02d}",
            decision_at=click,
            provider=cached,
            pit_store=pit,
            tape_policy=tape_policy,
            bridge_policy=bridge_policy,
            delta_oi_rows=None,
        )
        frozen = proof.get("frozen_thesis")
        if frozen is None:
            raise ValueError(f"ENRICHED_PROOF_INPUT_UNRESOLVED_AT_CLICK_{index}: {proof.get('reason')}")
        if proof.get("historical_memory_available") is not True:
            raise ValueError(f"ENRICHED_HISTORICAL_MEMORY_MISSING_AT_CLICK_{index}")
        if proof.get("options_context_available") is not True:
            raise ValueError(f"ENRICHED_OPTIONS_CONTEXT_MISSING_AT_CLICK_{index}")
        if proof.get("stablecoin_context_status") != "READY":
            raise ValueError(f"ENRICHED_STABLECOIN_CONTEXT_NOT_READY_AT_CLICK_{index}: {proof.get('stablecoin_context_status')}")

        visible = [row for row in fifteen_minute if row.available_at <= click]
        future = [row for row in fifteen_minute if click < row.available_at <= window_end]
        setup = build_btc_underlying_setup(
            click_id=f"enriched24h-underlying-15m-{index:02d}",
            decision_at=click,
            market_state=_market_state_from_frozen(frozen),
            completed_candles=visible,
            valid_until=window_end,
        )
        outcome = resolve_btc_underlying_setup(setup=setup, future_candles=future)
        decision = dict(frozen.get("decision") or {})
        results.append({
            "click_index": index,
            "decision_at": click.isoformat(),
            "proof_status": proof.get("status"),
            "available_lanes": list(decision.get("available_lanes") or []),
            "missing_lanes": list(decision.get("missing_lanes") or []),
            "historical_memory_available": proof.get("historical_memory_available"),
            "historical_memory_analogue_count": proof.get("historical_memory_analogue_count"),
            "historical_memory_mean_similarity": proof.get("historical_memory_mean_similarity"),
            "historical_memory_median_forward_return_pct": proof.get("historical_memory_median_forward_return_pct"),
            "derivatives_evidence_status": proof.get("derivatives_evidence_status"),
            "options_context_available": proof.get("options_context_available"),
            "options_context_status": proof.get("options_context_status"),
            "stablecoin_context_available": proof.get("stablecoin_context_available"),
            "stablecoin_context_status": proof.get("stablecoin_context_status"),
            "stablecoin_liquidity_state": proof.get("stablecoin_liquidity_state"),
            "setup": setup,
            "outcome": outcome,
        })
        await _progress(progress_callback, index + 1, CLICK_COUNT, "PROCESSING_ENRICHED_CLICKS")

    summary = _summary(results)
    return {
        "mode": MODE,
        "status": "COMPLETED",
        "window_start": start.isoformat(),
        "window_end_exclusive": window_end.isoformat(),
        "click_interval_minutes": 15,
        "scheduled_clicks": CLICK_COUNT,
        "summary": summary,
        "coverage": {
            "spot_1h_candles": len(one_hour),
            "spot_1m_candles": len(one_minute),
            "spot_15m_candles": len(fifteen_minute),
            "historical_memory_lookback_hours": int(bridge_policy.historical_memory_lookback_hours),
            "readiness_snapshot": readiness,
        },
        "methodology": {
            "underlying_direction_test_only": True,
            "later_prospective_pit_window": True,
            "original_first_day_window_modified": False,
            "completed_candles_only": True,
            "all_96_clicks_require_fresh_options_context": True,
            "all_96_clicks_require_24h_comparable_stablecoin_context": True,
            "historical_memory_direction_creator": False,
            "minimum_target1_r": 1.5,
            "retuned_after_outcomes": False,
        },
        "limitations": [
            "Macro, news, on-chain and social lanes remain missing unless separately captured and wired point-in-time.",
            "Deribit options context and aggregate stablecoin supply are context-only and cannot manufacture BTC direction.",
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


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "BTC_ENRICHED_24H_REPLAY_CONTRACT_V1",
        "original_first_24h_replay_mutated": False,
        "later_pit_window_only": True,
        "readiness_gate_required": True,
        "full_24h_outcome_window_must_be_observable": True,
        "options_context_fresh_at_every_click": True,
        "options_prior_iv_history_required": True,
        "stablecoin_context_fresh_at_every_click": True,
        "stablecoin_24h_comparison_required_at_every_click": True,
        "historical_memory_completed_coindcx_only": True,
        "historical_memory_may_create_direction": False,
        "options_context_may_create_direction": False,
        "stablecoin_context_may_create_direction": False,
        "options_profitability_evaluated": False,
        "futures_trade_generated": False,
        "broker_order_placement_allowed": False,
        "live_execution": False,
        "capital_committed_inr": 0,
        "research_only": True,
    }
