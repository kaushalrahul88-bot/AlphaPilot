"""Build frozen, outcome-blind V5 Historical Holdout A inputs.

This module prepares two disjoint June/July 2026 historical windows using the
already-frozen V5 Brain. It freezes decisions and five-minute future tapes but
never resolves outcomes or calculates performance.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import fno_15m_historical_replay_v1 as core
from . import fno_candle_only_four_stock_backtest_v1 as baseline
from . import fno_market_brain_v5 as brain
from .fno_market_brain_v3_current_expiry_dataset import _fetch, _tape_hash, context_at

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
PROTOCOL_ID = "FNO_MARKET_BRAIN_V5_HOLDOUT_A_2026-09-07"
BRAIN_FROZEN_COMMIT = "a5a39253c1aa79f6663cd11b41f7d6607b96af90"
KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "fno_market_brain_v5_holdout_a_knowledge.json"

FROZEN_STOCKS = (
    ("HDFCBANK", "PRIVATE_BANKING"),
    ("INFY", "INFORMATION_TECHNOLOGY"),
    ("MARUTI", "PASSENGER_AUTOMOBILES"),
    ("BHARTIARTL", "TELECOMMUNICATIONS"),
)
STOCKS = tuple(symbol for symbol, _ in FROZEN_STOCKS)
TIMEFRAMES = ("5m", "15m", "1h")
WINDOWS = (
    ("JUNE_2026", date(2026, 6, 1), date(2026, 6, 12)),
    ("JULY_2026", date(2026, 7, 1), date(2026, 7, 14)),
)
CLICKS_PER_DAY = 20
CLICK_START = time(9, 30)
CLICK_END = time(14, 0)
CLICK_STEP_MINUTES = 5

HISTORY_START = {
    "5m": datetime(2026, 5, 20, 9, 15, tzinfo=IST),
    "15m": datetime(2026, 5, 15, 9, 15, tzinfo=IST),
    "1h": datetime(2026, 3, 25, 9, 15, tzinfo=IST),
}
HISTORY_END = datetime(2026, 7, 14, 15, 30, tzinfo=IST)
CONTEXT_START = datetime(2026, 5, 15, 9, 15, tzinfo=IST)


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
        raise ValueError(f"{day.isoformat()} is outside frozen Holdout A windows")
    pool: list[datetime] = []
    cursor = datetime.combine(day, CLICK_START, tzinfo=IST)
    end = datetime.combine(day, CLICK_END, tzinfo=IST)
    while cursor <= end:
        pool.append(cursor.astimezone(UTC))
        cursor += timedelta(minutes=CLICK_STEP_MINUTES)
    seed = int.from_bytes(
        hashlib.sha256(f"{PROTOCOL_ID}:{day.isoformat()}".encode()).digest()[:8],
        "big",
    )
    return sorted(random.Random(seed).sample(pool, CLICKS_PER_DAY))


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
    output = []
    for row in rows:
        if not row:
            continue
        stamp = baseline._stamp(row[0])
        if stamp is None:
            continue
        local = stamp.astimezone(IST)
        if _eligible_day(local.date()) and time(9, 15) <= local.time() <= time(15, 30):
            output.append(row)
    return output


async def build_holdout_a_dataset(provider, progress=None) -> dict[str, Any]:
    knowledge = load_knowledge()
    categories = dict(FROZEN_STOCKS)

    histories: dict[str, dict[str, list[list]]] = {symbol: {} for symbol in STOCKS}
    errors: list[dict[str, Any]] = []
    tasks = [(symbol, timeframe) for symbol in STOCKS for timeframe in TIMEFRAMES]
    for index, (symbol, timeframe) in enumerate(tasks, start=1):
        if progress:
            await progress({
                "stage": "FETCHING_STOCK_HISTORY",
                "completed": index - 1,
                "total": len(tasks),
                "symbol": symbol,
                "timeframe": timeframe,
            })
        rows, failures = await _fetch(provider, symbol, timeframe, HISTORY_START[timeframe], HISTORY_END, progress)
        histories[symbol][timeframe] = rows
        if not rows:
            errors.append({"symbol": symbol, "timeframe": timeframe, "attempt_errors": failures})
    if errors:
        return {"protocol_id": PROTOCOL_ID, "status": "SOURCE_STOCK_DATA_INCOMPLETE", "history_errors": errors, "safety": architecture_contract()}

    session_sets = {symbol: set(sessions_from_tape(histories[symbol]["5m"])) for symbol in STOCKS}
    union = set().union(*session_sets.values())
    common = set.intersection(*session_sets.values()) if session_sets else set()
    if union != common or not common:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "SESSION_TAPE_MISMATCH",
            "sessions_by_stock": {symbol: sorted(day.isoformat() for day in days) for symbol, days in session_sets.items()},
            "safety": architecture_contract(),
        }
    sessions = sorted(common)
    window_sessions = {window_id: [day for day in sessions if start <= day <= end] for window_id, start, end in WINDOWS}
    if any(not days for days in window_sessions.values()):
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "HOLDOUT_WINDOW_EMPTY",
            "window_sessions": {key: [day.isoformat() for day in value] for key, value in window_sessions.items()},
            "safety": architecture_contract(),
        }

    context_symbols = {"NIFTY"}
    for symbol in STOCKS:
        context_symbols.update(knowledge["stocks"][symbol]["peer_basket"])
    context_histories: dict[str, list[list]] = {symbol: histories[symbol]["15m"] for symbol in STOCKS}
    for index, symbol in enumerate(sorted(context_symbols), start=1):
        if progress:
            await progress({"stage": "FETCHING_CONTEXT_HISTORY", "completed": index - 1, "total": len(context_symbols), "symbol": symbol})
        rows, failures = await _fetch(provider, symbol, "15m", CONTEXT_START, HISTORY_END, progress)
        context_histories[symbol] = rows
        if symbol == "NIFTY" and not rows:
            return {
                "protocol_id": PROTOCOL_ID,
                "status": "REQUIRED_CONTEXT_DATA_INCOMPLETE",
                "missing_required": [symbol],
                "attempt_errors": failures,
                "safety": architecture_contract(),
            }

    click_manifest = {day.isoformat(): [click.isoformat() for click in deterministic_clicks(day)] for day in sessions}
    rows_out: list[dict[str, Any]] = []
    raw_actions = {"LONG": 0, "SHORT": 0, "NO_TRADE": 0}
    effective_actions = {"LONG": 0, "SHORT": 0, "NO_TRADE": 0}
    duplicate_suppressions = 0

    for symbol in STOCKS:
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
                rows_out.append({
                    "window": _window_for_day(day),
                    "trade_date": day.isoformat(),
                    "click_at": click.isoformat(),
                    "symbol": symbol,
                    "category": categories[symbol],
                    "technical": technical,
                    "context": context,
                    "raw_decision": raw_decision,
                    "decision": decision,
                })

    expected = len(sessions) * CLICKS_PER_DAY * len(STOCKS)
    if len(rows_out) != expected:
        return {"protocol_id": PROTOCOL_ID, "status": "OBSERVATION_COUNT_MISMATCH", "observations": len(rows_out), "expected": expected, "safety": architecture_contract()}

    frozen_tape = {symbol: _frozen_tape_rows(histories[symbol]["5m"]) for symbol in STOCKS}
    return {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED",
        "brain_protocol_id": brain.PROTOCOL_ID,
        "brain_frozen_commit": BRAIN_FROZEN_COMMIT,
        "experiment": {
            "kind": "HISTORICAL_HOLDOUT_NOT_FORWARD_TEST",
            "stocks": [{"symbol": symbol, "category": category} for symbol, category in FROZEN_STOCKS],
            "windows": [
                {
                    "id": window_id,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "completed_sessions": [day.isoformat() for day in window_sessions[window_id]],
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
        "context_coverage": {symbol: len(history) for symbol, history in sorted(context_histories.items())},
        "frozen_5m_tape_hashes": {symbol: _tape_hash(frozen_tape[symbol]) for symbol in STOCKS},
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
        "live_execution": False,
        "capital_committed": 0,
    }
