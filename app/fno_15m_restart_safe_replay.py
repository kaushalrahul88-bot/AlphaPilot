"""Restart-safe orchestration helpers for the F&O 15-minute historical replay.

This module does not change strategy methodology. It adds a durable cache for
reconstructible Groww historical candles so an interrupted Render process can
resume missing history fetches instead of starting the network stage from zero.
The point-in-time option-chain archive remains read-only and is still the only
derivatives state admitted to decisions.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from . import fno_15m_historical_replay_v1 as core
from .fno_market_brain_v2 import build_experience_memory, build_perception, decide_shadow
from .fno_prospective_protocol_v1 import PRIMARY_HORIZON_MINUTES

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")
CACHE_VERSION = "FNO_15M_CANDLE_CACHE_V1"
ProgressCallback = Callable[[Mapping[str, Any]], Awaitable[None]]

CACHE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fno_15m_backtest_candle_cache_v1 (
    dataset_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candles JSONB NOT NULL DEFAULT '[]'::jsonb,
    fetch_error TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (dataset_key, symbol, timeframe)
);
"""


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


def _ensure_cache_schema_sync(database_url: str) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(CACHE_SCHEMA_SQL)
        conn.commit()


async def ensure_cache_schema(database_url: str) -> None:
    await asyncio.to_thread(_ensure_cache_schema_sync, database_url)


def dataset_key(trade_dates: list, symbols: Iterable[str]) -> str:
    if not trade_dates:
        return f"{CACHE_VERSION}:EMPTY"
    payload = {
        "cache_version": CACHE_VERSION,
        "mode": core.MODE,
        "first_trade_date": trade_dates[0].isoformat(),
        "last_trade_date": trade_dates[-1].isoformat(),
        "symbols": sorted(str(item).upper() for item in symbols),
        "timeframes": list(core.TIMEFRAMES),
        "lookback_days": dict(core.LOOKBACK_DAYS),
        "groww_interval": dict(core.GROWW_INTERVAL),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return f"{CACHE_VERSION}:{payload['first_trade_date']}:{payload['last_trade_date']}:{digest}"


def _load_cached_history_sync(
    database_url: str,
    key: str,
    symbol: str,
    timeframe: str,
) -> tuple[list[list], str | None] | None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT candles, fetch_error
                FROM fno_15m_backtest_candle_cache_v1
                WHERE dataset_key=%s AND symbol=%s AND timeframe=%s
                """,
                (key, symbol, timeframe),
            )
            row = cur.fetchone()
    if row is None:
        return None
    candles, error = row
    if isinstance(candles, str):
        candles = json.loads(candles)
    return list(candles or []), (str(error) if error else None)


async def load_cached_history(
    database_url: str,
    key: str,
    symbol: str,
    timeframe: str,
) -> tuple[list[list], str | None] | None:
    return await asyncio.to_thread(
        _load_cached_history_sync,
        database_url,
        key,
        symbol,
        timeframe,
    )


def _save_cached_history_sync(
    database_url: str,
    key: str,
    symbol: str,
    timeframe: str,
    candles: list[list],
    error: str | None,
) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fno_15m_backtest_candle_cache_v1
                    (dataset_key, symbol, timeframe, candles, fetch_error, fetched_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (dataset_key, symbol, timeframe)
                DO UPDATE SET
                    candles=EXCLUDED.candles,
                    fetch_error=EXCLUDED.fetch_error,
                    fetched_at=NOW()
                """,
                (key, symbol, timeframe, Jsonb(candles), error),
            )
        conn.commit()


async def save_cached_history(
    database_url: str,
    key: str,
    symbol: str,
    timeframe: str,
    candles: list[list],
    error: str | None,
) -> None:
    await asyncio.to_thread(
        _save_cached_history_sync,
        database_url,
        key,
        symbol,
        timeframe,
        candles,
        error,
    )


async def _progress(callback: ProgressCallback | None, payload: Mapping[str, Any]) -> None:
    if callback is not None:
        await callback(dict(payload))


async def fetch_all_histories_checkpointed(
    provider,
    symbols: Iterable[str],
    trade_dates: list,
    database_url: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> tuple[dict[str, dict[str, list[list]]], list[dict[str, Any]], str]:
    symbols = list(symbols)
    if not trade_dates:
        return {}, [], dataset_key([], symbols)

    await ensure_cache_schema(database_url)
    key = dataset_key(trade_dates, symbols)
    earliest = datetime.combine(trade_dates[0], time(9, 15), tzinfo=IST)
    latest = datetime.combine(trade_dates[-1], time(15, 30), tzinfo=IST)
    histories: dict[str, dict[str, list[list]]] = {}
    failures: list[dict[str, Any]] = []
    total = len(symbols) * len(core.TIMEFRAMES)
    completed = 0
    cache_hits = 0

    for symbol in symbols:
        histories[symbol] = {}
        for timeframe in core.TIMEFRAMES:
            cached = await load_cached_history(database_url, key, symbol, timeframe)
            if cached is not None:
                candles, cached_error = cached
                histories[symbol][timeframe] = candles
                cache_hits += 1
                if cached_error:
                    failures.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "error": cached_error,
                        "from_cache": True,
                    })
            else:
                start = earliest - timedelta(days=core.LOOKBACK_DAYS[timeframe])
                error = None
                try:
                    candles = await core.fetch_historical_candles(
                        provider,
                        symbol,
                        timeframe,
                        start,
                        latest,
                    )
                except Exception as exc:
                    candles = []
                    error = f"{exc.__class__.__name__}: {str(exc)[:240]}"
                    failures.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "error": error,
                        "from_cache": False,
                    })
                histories[symbol][timeframe] = candles
                await save_cached_history(
                    database_url,
                    key,
                    symbol,
                    timeframe,
                    candles,
                    error,
                )

            completed += 1
            await _progress(progress_callback, {
                "stage": "HISTORICAL_CANDLE_CHECKPOINTS",
                "dataset_key": key,
                "completed_histories": completed,
                "total_histories": total,
                "cache_hits": cache_hits,
                "last_symbol": symbol,
                "last_timeframe": timeframe,
            })

    return histories, failures, key


async def run_fno_15m_historical_replay_restart_safe(
    provider,
    database_url: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the frozen V1 replay with durable reconstructible-candle checkpoints."""
    if not str(database_url or "").strip():
        raise ValueError("database_url is required for F&O historical replay")

    await _progress(progress_callback, {"stage": "LOADING_POINT_IN_TIME_SNAPSHOTS"})
    snapshots_by_symbol, trade_dates = await core._load_snapshots(database_url)
    replayable_symbols = sorted(
        symbol for symbol in core.CONNECTED_UNIVERSE if snapshots_by_symbol.get(symbol)
    )
    missing_connected_symbols = sorted(
        set(core.CONNECTED_UNIVERSE) - set(replayable_symbols)
    )
    if not trade_dates or not replayable_symbols:
        return {
            "mode": core.MODE,
            "status": "NO_REPLAYABLE_DATA",
            "trade_dates": [item.isoformat() for item in trade_dates],
            "replayable_symbols": replayable_symbols,
            "missing_connected_symbols": missing_connected_symbols,
            "live_execution": False,
            "capital_committed": 0,
        }

    histories, history_failures, cache_key = await fetch_all_histories_checkpointed(
        provider,
        replayable_symbols,
        trade_dates,
        database_url,
        progress_callback=progress_callback,
    )
    await _progress(progress_callback, {
        "stage": "REPLAYING_DECISIONS",
        "scheduled_clicks": len(trade_dates) * len(core.click_schedule(trade_dates[0])),
        "processed_clicks": 0,
        "dataset_key": cache_key,
    })

    mode_configs = {
        "STRICT_V2": core.STRICT_MAX_SNAPSHOT_AGE_SECONDS,
        "COVERAGE_30M": core.DIAGNOSTIC_MAX_SNAPSHOT_AGE_SECONDS,
    }
    prior_cases: dict[str, list[dict[str, Any]]] = {mode: [] for mode in mode_configs}
    mode_clicks: dict[str, list[dict[str, Any]]] = {mode: [] for mode in mode_configs}
    mode_candidates: dict[str, list[dict[str, Any]]] = {mode: [] for mode in mode_configs}
    technical_cache: dict[tuple[str, str], dict[str, Any]] = {}
    processed_clicks = 0
    scheduled_clicks = len(trade_dates) * len(core.click_schedule(trade_dates[0]))

    for trade_date in trade_dates:
        for click_at in core.click_schedule(trade_date):
            states: list[dict[str, Any]] = []
            for symbol in replayable_symbols:
                snapshot = core._snapshot_before(snapshots_by_symbol[symbol], click_at)
                if snapshot is None:
                    states.append({"symbol": symbol, "snapshot": None})
                    continue
                cache_id = (symbol, click_at.isoformat())
                technical = technical_cache.get(cache_id)
                if technical is None:
                    technical = core.technical_at(
                        symbol,
                        histories.get(symbol, {}),
                        click_at,
                    )
                    technical_cache[cache_id] = technical
                perception = build_perception(
                    snapshot,
                    decision_at=click_at,
                    technical=technical,
                    external_context={
                        "historical_replay": True,
                        "news_replayed": False,
                        "macro_context_replayed": False,
                        "reason": "current V2 decision function does not consume external context",
                    },
                )
                states.append({
                    "symbol": symbol,
                    "snapshot": snapshot,
                    "technical": technical,
                    "perception": perception,
                })

            for mode, max_age in mode_configs.items():
                counts = Counter()
                blocker_counts = Counter()
                click_candidate_rows: list[dict[str, Any]] = []
                snapshot_available = 0

                for state in states:
                    symbol = state["symbol"]
                    if state.get("snapshot") is None:
                        counts["NO_SNAPSHOT"] += 1
                        continue
                    snapshot_available += 1
                    perception = state["perception"]
                    technical = state["technical"]
                    memory = build_experience_memory(
                        perception,
                        prior_cases[mode][-core.MAX_MEMORY_CASES:],
                        limit=5,
                    )
                    decision = decide_shadow(
                        perception,
                        memory,
                        max_snapshot_age_seconds=max_age,
                    )
                    action = str(decision.get("research_action") or "NO_TRADE")
                    counts[action] += 1
                    for blocker in decision.get("research_blockers") or []:
                        blocker_counts[str(blocker)] += 1

                    due = click_at + timedelta(minutes=PRIMARY_HORIZON_MINUTES)
                    outcome = core.resolve_historical_candidate(
                        perception,
                        decision,
                        snapshots_by_symbol[symbol],
                        histories.get(symbol, {}).get("5m", []),
                        click_at=click_at,
                    )
                    case = {
                        "perception": perception,
                        "research_action": action,
                        "outcome": outcome,
                        "outcome_available_at": due.isoformat(),
                    }
                    if action in {"BUY_CE", "BUY_PE"}:
                        row = core._candidate_row(
                            mode=mode,
                            click_at=click_at,
                            symbol=symbol,
                            perception=perception,
                            technical=technical,
                            decision=decision,
                            outcome=outcome,
                        )
                        click_candidate_rows.append(row)
                        mode_candidates[mode].append(row)
                    state.setdefault("cases_to_append", {})[mode] = case

                click_candidate_rows.sort(key=core._reporting_rank, reverse=True)
                mode_clicks[mode].append({
                    "trade_date": trade_date.isoformat(),
                    "click_at": click_at.isoformat(),
                    "click_ist": click_at.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                    "scheduled": True,
                    "symbols_in_replay_universe": len(replayable_symbols),
                    "snapshot_available_symbols": snapshot_available,
                    "research_action_counts": dict(counts),
                    "research_blocker_counts": dict(blocker_counts),
                    "actionable_candidates": len(click_candidate_rows),
                    "display_top_candidate": click_candidate_rows[0] if click_candidate_rows else None,
                    "display_top_candidate_is_policy_selection": False,
                })

            for state in states:
                for mode, case in (state.get("cases_to_append") or {}).items():
                    prior_cases[mode].append(case)

            processed_clicks += 1
            if processed_clicks == 1 or processed_clicks % 5 == 0 or processed_clicks == scheduled_clicks:
                await _progress(progress_callback, {
                    "stage": "REPLAYING_DECISIONS",
                    "dataset_key": cache_key,
                    "processed_clicks": processed_clicks,
                    "scheduled_clicks": scheduled_clicks,
                    "trade_date": trade_date.isoformat(),
                    "click_ist": click_at.astimezone(IST).strftime("%H:%M"),
                })

    symbol_slots = (
        len(trade_dates)
        * len(core.click_schedule(trade_dates[0]))
        * len(replayable_symbols)
    )
    results: dict[str, Any] = {}
    for mode in mode_configs:
        results[mode] = {
            "max_snapshot_age_seconds": mode_configs[mode],
            "summary": core._aggregate_mode(
                mode,
                mode_clicks[mode],
                mode_candidates[mode],
                symbol_slots,
            ),
            "daily": core._daily_summary(mode_clicks[mode], mode_candidates[mode]),
            "clicks": mode_clicks[mode],
            "candidate_results": mode_candidates[mode],
        }

    result = {
        "mode": core.MODE,
        "status": "COMPLETED",
        "generated_at": datetime.now(UTC).isoformat(),
        "methodology": {
            "click_schedule_ist": "09:30-15:00 inclusive every 15 minutes",
            "clicks_per_trading_day": len(core.click_schedule(trade_dates[0])),
            "trade_dates_source": "distinct dates present in fno_option_chain_snapshots",
            "option_chain_source": "saved point-in-time Groww snapshots only",
            "technical_source": "Groww reconstructible historical candles fetched after the fact",
            "technical_no_lookahead": "only fully completed bars; current candle excluded conservatively",
            "timeframes": list(core.TIMEFRAMES),
            "memory": "strictly prior and descriptive only; does not create/reverse decisions",
            "strict_snapshot_freshness_seconds": core.STRICT_MAX_SNAPSHOT_AGE_SECONDS,
            "diagnostic_snapshot_freshness_seconds": core.DIAGNOSTIC_MAX_SNAPSHOT_AGE_SECONDS,
            "primary_outcome_horizon_minutes": PRIMARY_HORIZON_MINUTES,
            "actionable_option_outcome": "same exact trading_symbol from later saved snapshots at/before horizon",
            "bid_ask_execution_pnl": "NOT_AVAILABLE_IN_HISTORICAL_ARCHIVE",
            "historical_option_chain_backfill": False,
            "external_news_macro_replay": False,
            "production_policy_changed": False,
            "restart_safe_candle_checkpointing": True,
            "candle_cache_dataset_key": cache_key,
        },
        "coverage": {
            "trade_dates": [item.isoformat() for item in trade_dates],
            "trading_days": len(trade_dates),
            "scheduled_clicks": scheduled_clicks,
            "connected_universe_size": len(core.CONNECTED_UNIVERSE),
            "replayable_symbols": replayable_symbols,
            "replayable_symbol_count": len(replayable_symbols),
            "missing_connected_symbols": missing_connected_symbols,
            "history_fetch_failures": history_failures,
        },
        "results": results,
        "safety": {
            "diagnostic_only": True,
            "ready_for_live_money": False,
            "live_execution": False,
            "capital_committed": 0,
            "futures_trade_generated": False,
            "point_in_time_source_database_writes": False,
            "orchestration_cache_writes": True,
        },
    }
    await _progress(progress_callback, {
        "stage": "COMPLETED",
        "dataset_key": cache_key,
        "processed_clicks": scheduled_clicks,
        "scheduled_clicks": scheduled_clicks,
    })
    return result


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_15M_RESTART_SAFE_REPLAY_V1_CONTRACT",
        "frozen_methodology": core.MODE,
        "strategy_logic_changed": False,
        "point_in_time_option_chain_read_only": True,
        "reconstructible_candle_cache_writes": True,
        "cache_key_is_dataset_scoped": True,
        "resume_skips_completed_history_fetches": True,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generation": False,
    }
