"""Build frozen, outcome-blind V7 Historical Holdout C inputs.

Holdout C is a fresh historical promotion dataset for frozen V7. It uses twelve
new target stocks and disjoint January-March 2026 windows. The module freezes V7
decisions and future 5-minute cash tapes but never resolves outcomes or computes
performance.

History transport reuses the already-tested bounded/resumable Holdout B helper.
That helper is operational only: it cannot alter the Holdout C protocol, dates,
clicks, point-in-time context, V7 Brain or frozen future tape.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from . import fno_15m_historical_replay_v1 as core
from . import fno_candle_only_four_stock_backtest_v1 as baseline
from . import fno_market_brain_v7 as brain
from .fno_market_brain_v3_current_expiry_dataset import _tape_hash, context_at
from .fno_market_brain_v6_holdout_b_dataset import (
    FETCH_CONCURRENCY,
    _bounded_fetch_many,
    _fetch_cached,
    _safe_progress,
)

IST = ZoneInfo("Asia/Kolkata")

PROTOCOL_ID = "FNO_MARKET_BRAIN_V7_HOLDOUT_C_2026-09-08"
BRAIN_FROZEN_COMMIT = "461053bb98ef60ffdad9f9bd34ce4a5e599016c3"
KNOWLEDGE_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "fno_market_brain_v7_holdout_c_knowledge.json"
)

FROZEN_STOCKS = (
    ("ICICIBANK", "PRIVATE_BANKING"),
    ("TCS", "INFORMATION_TECHNOLOGY"),
    ("M&M", "AUTOMOBILES"),
    ("HINDUNILVR", "FMCG_CONSUMER"),
    ("CIPLA", "PHARMACEUTICALS"),
    ("HINDALCO", "METALS_ALUMINIUM"),
    ("NTPC", "POWER_GENERATION"),
    ("TITAN", "CONSUMER_DISCRETIONARY"),
    ("ASIANPAINT", "PAINTS_HOME_IMPROVEMENT"),
    ("ADANIPORTS", "PORTS_LOGISTICS"),
    ("COALINDIA", "COAL_MINING_ENERGY"),
    ("BRITANNIA", "FMCG_FOODS"),
)
STOCKS = tuple(symbol for symbol, _ in FROZEN_STOCKS)
TIMEFRAMES = ("5m", "15m", "1h")
WINDOWS = (
    ("JANUARY_2026", date(2026, 1, 5), date(2026, 1, 30)),
    ("FEBRUARY_2026", date(2026, 2, 2), date(2026, 2, 27)),
    ("MARCH_2026", date(2026, 3, 2), date(2026, 3, 27)),
)
CLICKS_PER_DAY = 20
CLICK_START = time(9, 30)
CLICK_END = time(14, 0)
CLICK_STEP_MINUTES = 5

# Warm-up ranges are fixed before outcomes. They provide more history than the
# existing technical lookback semantics require at the first January click.
HISTORY_START = {
    "5m": datetime(2025, 12, 15, 9, 15, tzinfo=IST),
    "15m": datetime(2025, 12, 1, 9, 15, tzinfo=IST),
    "1h": datetime(2025, 10, 15, 9, 15, tzinfo=IST),
}
HISTORY_END = datetime(2026, 3, 27, 15, 30, tzinfo=IST)
CONTEXT_START = datetime(2025, 12, 1, 9, 15, tzinfo=IST)

HistoryCacheGet = Callable[[str, str, datetime, datetime], Awaitable[list[list] | None]]
HistoryCachePut = Callable[[str, str, datetime, datetime, list[list]], Awaitable[None]]
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


def load_knowledge(path: Path = KNOWLEDGE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _window_for_day(day: date) -> str | None:
    for window_id, start, end in WINDOWS:
        if start <= day <= end:
            return window_id
    return None


def _eligible_day(day: date) -> bool:
    return _window_for_day(day) is not None


def deterministic_clicks(day: date) -> list[datetime]:
    if not _eligible_day(day):
        raise ValueError(f"{day.isoformat()} is outside frozen Holdout C windows")
    pool: list[datetime] = []
    cursor = datetime.combine(day, CLICK_START, tzinfo=IST)
    end = datetime.combine(day, CLICK_END, tzinfo=IST)
    while cursor <= end:
        pool.append(cursor)
        cursor = cursor.replace() + __import__("datetime").timedelta(minutes=CLICK_STEP_MINUTES)
    seed = int.from_bytes(
        hashlib.sha256(f"{PROTOCOL_ID}:{day.isoformat()}".encode()).digest()[:8],
        "big",
    )
    return sorted(
        click.astimezone(__import__("datetime").timezone.utc)
        for click in random.Random(seed).sample(pool, CLICKS_PER_DAY)
    )


def sessions_from_tape(candles: list[list]) -> list[date]:
    output: set[date] = set()
    for row in candles:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        stamp = baseline._stamp(row[0])
        if stamp is None:
            continue
        local = stamp.astimezone(IST)
        if _eligible_day(local.date()) and time(9, 15) <= local.time() <= time(15, 30):
            output.add(local.date())
    return sorted(output)


def _frozen_tape_rows(rows: list[list]) -> list[list]:
    output: list[list] = []
    for row in rows:
        if not row:
            continue
        stamp = baseline._stamp(row[0])
        if stamp is None:
            continue
        local = stamp.astimezone(IST)
        if _eligible_day(local.date()) and time(9, 15) <= local.time() <= time(15, 30):
            output.append(list(row))
    return output


async def build_holdout_c_dataset(
    provider,
    progress: ProgressCallback | None = None,
    cache_get: HistoryCacheGet | None = None,
    cache_put: HistoryCachePut | None = None,
) -> dict[str, Any]:
    knowledge = load_knowledge()
    categories = dict(FROZEN_STOCKS)

    if tuple(knowledge.get("stocks", {}).keys()) != STOCKS:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "KNOWLEDGE_TARGET_UNIVERSE_MISMATCH",
            "safety": architecture_contract(),
        }

    histories: dict[str, dict[str, list[list]]] = {symbol: {} for symbol in STOCKS}
    errors: list[dict[str, Any]] = []
    stock_tasks = [(symbol, timeframe) for symbol in STOCKS for timeframe in TIMEFRAMES]

    await _safe_progress(
        progress,
        {
            "stage": "FETCHING_STOCK_HISTORY",
            "completed": 0,
            "total": len(stock_tasks),
            "concurrency": FETCH_CONCURRENCY,
        },
    )

    async def fetch_stock(item):
        symbol, timeframe = item
        return await _fetch_cached(
            provider,
            symbol,
            timeframe,
            HISTORY_START[timeframe],
            HISTORY_END,
            progress,
            cache_get,
            cache_put,
        )

    stock_results = await _bounded_fetch_many(stock_tasks, fetch_stock)
    for item, (rows, failures) in stock_results:
        symbol, timeframe = item
        histories[symbol][timeframe] = rows
        if not rows:
            errors.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "attempt_errors": failures,
                }
            )
    if errors:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "SOURCE_STOCK_DATA_INCOMPLETE",
            "history_errors": errors,
            "safety": architecture_contract(),
        }

    session_sets = {
        symbol: set(sessions_from_tape(histories[symbol]["5m"]))
        for symbol in STOCKS
    }
    union = set().union(*session_sets.values())
    common = set.intersection(*session_sets.values()) if session_sets else set()
    if union != common or not common:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "SESSION_TAPE_MISMATCH",
            "sessions_by_stock": {
                symbol: sorted(day.isoformat() for day in days)
                for symbol, days in session_sets.items()
            },
            "safety": architecture_contract(),
        }

    sessions = sorted(common)
    window_sessions = {
        window_id: [day for day in sessions if start <= day <= end]
        for window_id, start, end in WINDOWS
    }
    if any(not days for days in window_sessions.values()):
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "HOLDOUT_WINDOW_EMPTY",
            "window_sessions": {
                key: [day.isoformat() for day in value]
                for key, value in window_sessions.items()
            },
            "safety": architecture_contract(),
        }

    context_symbols = {"NIFTY"}
    for symbol in STOCKS:
        context_symbols.update(knowledge["stocks"][symbol]["peer_basket"])
    context_histories: dict[str, list[list]] = {
        symbol: histories[symbol]["15m"] for symbol in STOCKS
    }
    # Target stocks already have a frozen 15m tape. Do not refetch them when
    # another target also uses them as a peer.
    context_items = sorted(context_symbols - set(STOCKS))

    await _safe_progress(
        progress,
        {
            "stage": "FETCHING_CONTEXT_HISTORY",
            "completed": 0,
            "total": len(context_items),
            "concurrency": FETCH_CONCURRENCY,
        },
    )

    async def fetch_context(symbol):
        return await _fetch_cached(
            provider,
            symbol,
            "15m",
            CONTEXT_START,
            HISTORY_END,
            progress,
            cache_get,
            cache_put,
        )

    context_results = await _bounded_fetch_many(context_items, fetch_context)
    missing_context: list[dict[str, Any]] = []
    for symbol, (rows, failures) in context_results:
        context_histories[symbol] = rows
        if not rows:
            missing_context.append({"symbol": symbol, "attempt_errors": failures})

    if not context_histories.get("NIFTY"):
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "REQUIRED_CONTEXT_DATA_INCOMPLETE",
            "missing_required": ["NIFTY"],
            "missing_context": missing_context,
            "safety": architecture_contract(),
        }

    click_manifest = {
        day.isoformat(): [click.isoformat() for click in deterministic_clicks(day)]
        for day in sessions
    }
    rows_out: list[dict[str, Any]] = []
    raw_actions = {"LONG": 0, "SHORT": 0, "NO_TRADE": 0}
    effective_actions = {"LONG": 0, "SHORT": 0, "NO_TRADE": 0}
    duplicate_suppressions = 0

    for symbol_index, symbol in enumerate(STOCKS, start=1):
        await _safe_progress(
            progress,
            {
                "stage": "BUILDING_FROZEN_DECISIONS",
                "completed_stocks": symbol_index - 1,
                "total_stocks": len(STOCKS),
                "symbol": symbol,
            },
        )
        for day in sessions:
            tracker = brain.ThesisTracker()
            for click in deterministic_clicks(day):
                technical = core.technical_at(symbol, histories[symbol], click)
                context = context_at(symbol, click, context_histories, knowledge)
                raw_decision = brain.decide(symbol, technical, context)
                decision = tracker.apply(raw_decision)
                raw_action = str(raw_decision.get("action") or "NO_TRADE")
                effective_action = str(decision.get("action") or "NO_TRADE")
                raw_actions[raw_action] = raw_actions.get(raw_action, 0) + 1
                effective_actions[effective_action] = effective_actions.get(effective_action, 0) + 1
                if "DUPLICATE_ACTIVE_THESIS" in (decision.get("reasons") or []):
                    duplicate_suppressions += 1
                rows_out.append(
                    {
                        "window": _window_for_day(day),
                        "trade_date": day.isoformat(),
                        "click_at": click.isoformat(),
                        "symbol": symbol,
                        "category": categories[symbol],
                        "technical": technical,
                        "context": context,
                        "raw_decision": raw_decision,
                        "decision": decision,
                    }
                )

    expected = len(sessions) * CLICKS_PER_DAY * len(STOCKS)
    if len(rows_out) != expected:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "OBSERVATION_COUNT_MISMATCH",
            "observations": len(rows_out),
            "expected": expected,
            "safety": architecture_contract(),
        }

    await _safe_progress(progress, {"stage": "FINALIZING_FROZEN_TAPES"})
    frozen_tape = {
        symbol: _frozen_tape_rows(histories[symbol]["5m"])
        for symbol in STOCKS
    }

    return {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED",
        "brain_protocol_id": brain.PROTOCOL_ID,
        "brain_frozen_commit": BRAIN_FROZEN_COMMIT,
        "experiment": {
            "kind": "HISTORICAL_HOLDOUT_NOT_FORWARD_TEST",
            "stocks": [
                {"symbol": symbol, "category": category}
                for symbol, category in FROZEN_STOCKS
            ],
            "windows": [
                {
                    "id": window_id,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "completed_sessions": [
                        day.isoformat() for day in window_sessions[window_id]
                    ],
                    "session_count": len(window_sessions[window_id]),
                }
                for window_id, start, end in WINDOWS
            ],
            "session_count": len(sessions),
            "clicks_per_session": CLICKS_PER_DAY,
            "observations_per_stock": len(sessions) * CLICKS_PER_DAY,
            "observations": len(rows_out),
            "same_click_times_across_stocks": True,
            "thesis_tracker_reset_each_stock_session": True,
            "future_outcomes_resolved": False,
            "evaluation_metrics_computed": False,
        },
        "decision_counts": {
            "raw": raw_actions,
            "effective_after_thesis_dedup": effective_actions,
            "duplicate_suppressions": duplicate_suppressions,
        },
        "click_manifest": click_manifest,
        "knowledge": knowledge,
        "data_coverage": {
            symbol: {
                timeframe: {
                    "candles": len(histories[symbol][timeframe]),
                    "first": str(histories[symbol][timeframe][0][0]) if histories[symbol][timeframe] else None,
                    "last": str(histories[symbol][timeframe][-1][0]) if histories[symbol][timeframe] else None,
                }
                for timeframe in TIMEFRAMES
            }
            for symbol in STOCKS
        },
        "context_coverage": {
            symbol: len(history)
            for symbol, history in sorted(context_histories.items())
        },
        "missing_optional_context": missing_context,
        "frozen_5m_tape_hashes": {
            symbol: _tape_hash(frozen_tape[symbol]) for symbol in STOCKS
        },
        "frozen_5m_tape_by_stock": frozen_tape,
        "rows": rows_out,
        "safety": architecture_contract(),
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "brain_protocol_id": brain.PROTOCOL_ID,
        "brain_frozen_commit": BRAIN_FROZEN_COMMIT,
        "historical_backtest_dataset_only": True,
        "forward_test": False,
        "windows_frozen_before_outcomes": True,
        "stocks_frozen": [symbol for symbol, _ in FROZEN_STOCKS],
        "completed_candles_only": True,
        "point_in_time_context_only": True,
        "unknown_news_time_policy": "NEXT_NSE_SESSION_OPEN",
        "clicks_deterministic_and_frozen": True,
        "same_click_times_across_stocks": True,
        "thesis_deduplication_enabled": True,
        "future_tape_frozen": True,
        "future_outcomes_resolved": False,
        "evaluation_metrics_computed": False,
        "options_read_for_decision": False,
        "futures_read_for_decision": False,
        "v3_development_rows_used_as_holdout": False,
        "v5_holdout_a_rows_used_as_holdout": False,
        "v6_holdout_b_rows_used_as_holdout": False,
        "live_execution": False,
        "capital_committed": 0,
        "history_transport_cache_only": True,
        "history_cache_may_change_decisions": False,
        "bounded_fetch_concurrency": FETCH_CONCURRENCY,
    }
