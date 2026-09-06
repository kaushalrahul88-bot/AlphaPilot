"""Memory-bounded restart-safe F&O 15-minute historical replay.

This is an execution-reliability wrapper around the frozen V1 methodology. It
keeps the point-in-time option-chain archive read-only but avoids loading every
saved chain payload for every day into one process. Trading dates and symbols
are discovered from market-hours metadata only; full payloads are loaded one
trading day at a time and released before the next day.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping
from zoneinfo import ZoneInfo

from . import fno_15m_historical_replay_v1 as core
from .fno_15m_restart_safe_replay import fetch_all_histories_checkpointed
from .fno_market_brain_v2 import build_experience_memory, build_perception, decide_shadow
from .fno_prospective_protocol_v1 import PRIMARY_HORIZON_MINUTES

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")
ProgressCallback = Callable[[Mapping[str, Any]], Awaitable[None]]

REPLAY_INDEX_SQL = """
SELECT DISTINCT underlying_symbol
FROM fno_option_chain_snapshots
WHERE underlying_symbol = ANY(%s)
  AND (observed_at AT TIME ZONE 'Asia/Kolkata')::time
      BETWEEN TIME '09:15:00' AND TIME '15:30:00'
ORDER BY underlying_symbol;
"""

TRADE_DATES_SQL = """
SELECT DISTINCT (observed_at AT TIME ZONE 'Asia/Kolkata')::date AS trade_date
FROM fno_option_chain_snapshots
WHERE underlying_symbol = ANY(%s)
  AND (observed_at AT TIME ZONE 'Asia/Kolkata')::time
      BETWEEN TIME '09:15:00' AND TIME '15:30:00'
ORDER BY trade_date;
"""

DAY_SNAPSHOT_SQL = """
SELECT provider, underlying_symbol, expiry_date, observed_at, payload
FROM fno_option_chain_snapshots
WHERE underlying_symbol = ANY(%s)
  AND observed_at >= %s
  AND observed_at <= %s
ORDER BY underlying_symbol, observed_at;
"""


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


def _load_replay_index_sync(database_url: str) -> tuple[list[str], list[date]]:
    connected = list(core.CONNECTED_UNIVERSE)
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(REPLAY_INDEX_SQL, (connected,))
            symbols = [str(row[0]).upper() for row in cur.fetchall()]
            cur.execute(TRADE_DATES_SQL, (connected,))
            trade_dates = [row[0] for row in cur.fetchall()]
    return symbols, trade_dates


async def load_replay_index(database_url: str) -> tuple[list[str], list[date]]:
    return await asyncio.to_thread(_load_replay_index_sync, database_url)


def _load_day_snapshots_sync(
    database_url: str,
    symbols: list[str],
    trade_date: date,
) -> dict[str, list[dict[str, Any]]]:
    start_local = datetime.combine(trade_date, time(9, 0), tzinfo=IST)
    end_local = datetime.combine(trade_date, time(15, 30, 59), tzinfo=IST)
    start_utc = start_local.astimezone(UTC)
    end_utc = end_local.astimezone(UTC)
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(DAY_SNAPSHOT_SQL, (symbols, start_utc, end_utc))
            rows = cur.fetchall()

    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for provider, symbol, expiry, observed_at, payload in rows:
        observed = core._utc(observed_at)
        raw_payload = payload
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)
        by_symbol[str(symbol).upper()].append({
            "provider": str(provider or "GROWW"),
            "underlying_symbol": str(symbol).upper(),
            "expiry_date": expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry),
            "observed_at": observed,
            "payload": raw_payload,
        })
    return dict(by_symbol)


async def load_day_snapshots(
    database_url: str,
    symbols: list[str],
    trade_date: date,
) -> dict[str, list[dict[str, Any]]]:
    return await asyncio.to_thread(
        _load_day_snapshots_sync,
        database_url,
        list(symbols),
        trade_date,
    )


async def _progress(callback: ProgressCallback | None, payload: Mapping[str, Any]) -> None:
    if callback is not None:
        await callback(dict(payload))


def _cap_memory(cases: list[dict[str, Any]]) -> None:
    overflow = len(cases) - core.MAX_MEMORY_CASES
    if overflow > 0:
        del cases[:overflow]


async def run_fno_15m_historical_replay_restart_safe_v2(
    provider,
    database_url: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not str(database_url or "").strip():
        raise ValueError("database_url is required for F&O historical replay")

    await _progress(progress_callback, {"stage": "DISCOVERING_MARKET_HOURS_REPLAY_INDEX"})
    replayable_symbols, trade_dates = await load_replay_index(database_url)
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

    mode_configs = {
        "STRICT_V2": core.STRICT_MAX_SNAPSHOT_AGE_SECONDS,
        "COVERAGE_30M": core.DIAGNOSTIC_MAX_SNAPSHOT_AGE_SECONDS,
    }
    prior_cases: dict[str, list[dict[str, Any]]] = {mode: [] for mode in mode_configs}
    mode_clicks: dict[str, list[dict[str, Any]]] = {mode: [] for mode in mode_configs}
    mode_candidates: dict[str, list[dict[str, Any]]] = {mode: [] for mode in mode_configs}
    scheduled_clicks = len(trade_dates) * len(core.click_schedule(trade_dates[0]))
    processed_clicks = 0

    await _progress(progress_callback, {
        "stage": "REPLAYING_DECISIONS_BY_TRADING_DAY",
        "dataset_key": cache_key,
        "processed_clicks": 0,
        "scheduled_clicks": scheduled_clicks,
        "trading_days": len(trade_dates),
    })

    for day_number, trade_date in enumerate(trade_dates, start=1):
        day_snapshots = await load_day_snapshots(
            database_url,
            replayable_symbols,
            trade_date,
        )
        day_snapshot_rows = sum(len(rows) for rows in day_snapshots.values())
        await _progress(progress_callback, {
            "stage": "REPLAYING_DECISIONS_BY_TRADING_DAY",
            "dataset_key": cache_key,
            "day_number": day_number,
            "trading_days": len(trade_dates),
            "trade_date": trade_date.isoformat(),
            "day_snapshot_rows": day_snapshot_rows,
            "processed_clicks": processed_clicks,
            "scheduled_clicks": scheduled_clicks,
        })

        for click_at in core.click_schedule(trade_date):
            states: list[dict[str, Any]] = []
            for symbol in replayable_symbols:
                snapshots = day_snapshots.get(symbol, [])
                snapshot = core._snapshot_before(snapshots, click_at)
                if snapshot is None:
                    states.append({"symbol": symbol, "snapshot": None, "snapshots": snapshots})
                    continue
                technical = core.technical_at(
                    symbol,
                    histories.get(symbol, {}),
                    click_at,
                )
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
                    "snapshots": snapshots,
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
                        prior_cases[mode],
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
                        state["snapshots"],
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
                    _cap_memory(prior_cases[mode])

            processed_clicks += 1
            if processed_clicks == 1 or processed_clicks % 5 == 0 or processed_clicks == scheduled_clicks:
                await _progress(progress_callback, {
                    "stage": "REPLAYING_DECISIONS_BY_TRADING_DAY",
                    "dataset_key": cache_key,
                    "day_number": day_number,
                    "trading_days": len(trade_dates),
                    "trade_date": trade_date.isoformat(),
                    "day_snapshot_rows": day_snapshot_rows,
                    "processed_clicks": processed_clicks,
                    "scheduled_clicks": scheduled_clicks,
                    "click_ist": click_at.astimezone(IST).strftime("%H:%M"),
                    "memory_cases": {mode: len(rows) for mode, rows in prior_cases.items()},
                })

        # Release full option-chain payloads for this trading day before the next.
        day_snapshots.clear()

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
            "trade_dates_source": "distinct market-hours dates in fno_option_chain_snapshots",
            "trading_date_market_hours_ist": "09:15-15:30",
            "option_chain_source": "saved point-in-time Groww snapshots only",
            "option_chain_memory_scope": "one trading day at a time",
            "technical_source": "Groww reconstructible historical candles fetched after the fact",
            "technical_no_lookahead": "only fully completed bars; current candle excluded conservatively",
            "timeframes": list(core.TIMEFRAMES),
            "memory": f"strictly prior/descriptive only; in-memory case window capped at {core.MAX_MEMORY_CASES}",
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
        "version": "FNO_15M_RESTART_SAFE_REPLAY_V2_DAY_BOUNDED",
        "frozen_strategy_logic": True,
        "trading_dates_market_hours_only": True,
        "point_in_time_option_chain_read_only": True,
        "option_chain_payload_scope": "ONE_TRADING_DAY",
        "memory_case_window": core.MAX_MEMORY_CASES,
        "reconstructible_candle_cache_writes": True,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generation": False,
    }
