"""Prepare a frozen, outcome-blind V3 dataset for four unseen F&O stocks.

This module fetches historical cash candles for the current September 2026 stock
F&O cycle, creates 20 deterministic random clicks per completed trading session,
and snapshots the frozen V3 Market Brain using only completed pre-click candles
plus point-in-time market/peer/news context. It deliberately does NOT resolve
future outcomes or calculate backtest performance; evaluation criteria remain a
separate step.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from . import fno_15m_historical_replay_v1 as core
from . import fno_candle_only_four_stock_backtest_v1 as baseline
from . import fno_four_stock_context_enrichment_v1 as context_v1
from . import fno_market_brain_v3 as brain

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
PROTOCOL_ID = "FNO_V3_NEW_STOCK_CURRENT_EXPIRY_DATASET_2026-09-07"
EXPIRY_START = date(2026, 8, 26)
EXPIRY_DATE = date(2026, 9, 29)
DATA_END = date(2026, 9, 7)
CLICKS_PER_DAY = 20
CLICK_START = time(9, 30)
CLICK_END = time(14, 0)
CLICK_STEP_MINUTES = 5
FETCH_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (2, 5, 10)
KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "fno_market_brain_v3_new_stock_knowledge.json"

FROZEN_STOCKS = (
    ("HDFCBANK", "PRIVATE_BANKING"),
    ("INFY", "INFORMATION_TECHNOLOGY"),
    ("MARUTI", "PASSENGER_AUTOMOBILES"),
    ("BHARTIARTL", "TELECOMMUNICATIONS"),
)
STOCKS = tuple(symbol for symbol, _ in FROZEN_STOCKS)
TIMEFRAMES = ("5m", "15m", "1h")


def load_knowledge(path: Path = KNOWLEDGE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def deterministic_clicks(day: date) -> list[datetime]:
    pool: list[datetime] = []
    cursor = datetime.combine(day, CLICK_START, tzinfo=IST)
    end = datetime.combine(day, CLICK_END, tzinfo=IST)
    while cursor <= end:
        pool.append(cursor.astimezone(UTC))
        cursor += timedelta(minutes=CLICK_STEP_MINUTES)
    seed = int.from_bytes(hashlib.sha256(f"{PROTOCOL_ID}:{day.isoformat()}".encode()).digest()[:8], "big")
    return sorted(random.Random(seed).sample(pool, CLICKS_PER_DAY))


def _sessions(candles: list[list]) -> list[date]:
    output = set()
    for row in candles:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        stamp = baseline._stamp(row[0])
        if not stamp:
            continue
        local = stamp.astimezone(IST)
        if EXPIRY_START <= local.date() <= DATA_END and time(9, 15) <= local.time() <= time(15, 30):
            output.add(local.date())
    return sorted(output)


async def _fetch(provider, symbol: str, timeframe: str, start: datetime, end: datetime, progress=None):
    failures = []
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            rows = await baseline._chunk(provider, symbol, timeframe, start, end)
            if rows:
                return rows, failures
            failures.append(f"attempt {attempt}: EMPTY_TAPE")
        except Exception as exc:
            failures.append(f"attempt {attempt}: {exc.__class__.__name__}: {str(exc)[:400]}")
        if attempt < FETCH_ATTEMPTS:
            delay = RETRY_DELAYS_SECONDS[attempt - 1]
            if progress:
                await progress({"stage": "RETRYING_HISTORY", "symbol": symbol, "timeframe": timeframe, "next_attempt": attempt + 1, "delay_seconds": delay, "last_error": failures[-1]})
            await asyncio.sleep(delay)
    return [], failures


def _event_applies(event: Mapping[str, Any], symbol: str, category: str) -> bool:
    event_symbol = str(event.get("symbol") or "").upper()
    if event_symbol in {symbol, "MARKET"}:
        return True
    scope = str(event.get("scope") or "").upper()
    return scope == f"SECTOR:{category}"


def events_at(symbol: str, category: str, click: datetime, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observed = context_v1._dt(click)
    output = []
    for event in events:
        if not _event_applies(event, symbol, category):
            continue
        effective = context_v1._dt(event["effective_at"])
        if effective > observed:
            continue
        age = observed - effective
        if age.total_seconds() < 0 or age > timedelta(days=context_v1.EVENT_MAX_AGE_DAYS):
            continue
        output.append({**event, "age_hours": round(age.total_seconds() / 3600.0, 3)})
    return sorted(output, key=lambda item: item["effective_at"])


def context_at(symbol: str, click: datetime, histories: Mapping[str, list[list]], knowledge: Mapping[str, Any]) -> dict[str, Any]:
    profile = knowledge["stocks"][symbol]
    category = str(profile["category"])
    peers = list(profile["peer_basket"])
    market = context_v1._momentum(histories.get("NIFTY", []), click)
    stock = context_v1._momentum(histories.get(symbol, []), click)
    peer_values_by_symbol = {peer: context_v1._momentum(histories.get(peer, []), click) for peer in peers}
    values = [value for value in peer_values_by_symbol.values() if value is not None]
    peer_mean = round(fmean(values), 6) if values else None
    peer_breadth = round(sum(1 if value > 0 else -1 if value < 0 else 0 for value in values) / len(values), 6) if values else None
    relative = round(stock - market, 6) if stock is not None and market is not None else None
    eligible_events = events_at(symbol, category, click, list(knowledge.get("events") or []))
    components = {
        "market": context_v1._sign(market),
        "sector": context_v1._sign(peer_mean),
        "relative_strength": context_v1._sign(relative, context_v1.RELATIVE_STRENGTH_DEADBAND_PCT),
        "events": context_v1._event_score(eligible_events),
    }
    return {
        "market_60m_pct": market,
        "stock_60m_pct": stock,
        "relative_strength_vs_nifty_pct": relative,
        "peer_60m_pct": peer_values_by_symbol,
        "peer_mean_60m_pct": peer_mean,
        "peer_breadth": peer_breadth,
        "events": eligible_events,
        "components": components,
        "context_score": sum(components.values()),
        "complete": market is not None and stock is not None and len(values) >= max(2, len(peers) - 1),
        "knowledge_profile": {"category": category, "business": profile.get("business"), "primary_drivers": profile.get("primary_drivers", [])},
    }


def _tape_hash(rows: list[list]) -> str:
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


async def build_current_expiry_dataset(provider, progress=None) -> dict[str, Any]:
    knowledge = load_knowledge()
    categories = dict(FROZEN_STOCKS)
    end = datetime.combine(DATA_END, time(15, 30), tzinfo=IST)
    starts = {
        "5m": datetime.combine(EXPIRY_START - timedelta(days=55), time(9, 15), tzinfo=IST),
        "15m": datetime.combine(EXPIRY_START - timedelta(days=20), time(9, 15), tzinfo=IST),
        "1h": datetime.combine(EXPIRY_START - timedelta(days=65), time(9, 15), tzinfo=IST),
    }
    histories: dict[str, dict[str, list[list]]] = {symbol: {} for symbol in STOCKS}
    errors = []
    tasks = [(symbol, tf) for symbol in STOCKS for tf in TIMEFRAMES]
    for index, (symbol, timeframe) in enumerate(tasks, start=1):
        if progress:
            await progress({"stage": "FETCHING_STOCK_HISTORY", "completed": index - 1, "total": len(tasks), "symbol": symbol, "timeframe": timeframe})
        rows, failures = await _fetch(provider, symbol, timeframe, starts[timeframe], end, progress)
        histories[symbol][timeframe] = rows
        if not rows:
            errors.append({"symbol": symbol, "timeframe": timeframe, "attempt_errors": failures})
    if errors:
        return {"protocol_id": PROTOCOL_ID, "status": "SOURCE_STOCK_DATA_INCOMPLETE", "history_errors": errors, "safety": architecture_contract()}

    session_sets = {symbol: set(_sessions(histories[symbol]["5m"])) for symbol in STOCKS}
    union = set().union(*session_sets.values())
    common = set.intersection(*session_sets.values()) if session_sets else set()
    if union != common or not common:
        return {"protocol_id": PROTOCOL_ID, "status": "SESSION_TAPE_MISMATCH", "sessions_by_stock": {s: sorted(d.isoformat() for d in ds) for s, ds in session_sets.items()}, "safety": architecture_contract()}
    sessions = sorted(common)

    context_symbols = {"NIFTY"}
    for symbol in STOCKS:
        context_symbols.update(knowledge["stocks"][symbol]["peer_basket"])
    context_histories: dict[str, list[list]] = {symbol: histories[symbol]["15m"] for symbol in STOCKS}
    context_start = datetime.combine(EXPIRY_START - timedelta(days=10), time(9, 15), tzinfo=IST)
    for index, symbol in enumerate(sorted(context_symbols), start=1):
        if progress:
            await progress({"stage": "FETCHING_CONTEXT_HISTORY", "completed": index - 1, "total": len(context_symbols), "symbol": symbol})
        rows, failures = await _fetch(provider, symbol, "15m", context_start, end, progress)
        context_histories[symbol] = rows
        if symbol == "NIFTY" and not rows:
            return {"protocol_id": PROTOCOL_ID, "status": "REQUIRED_CONTEXT_DATA_INCOMPLETE", "missing_required": [symbol], "attempt_errors": failures, "safety": architecture_contract()}

    rows = []
    clicks_manifest = {day.isoformat(): [click.isoformat() for click in deterministic_clicks(day)] for day in sessions}
    expected = len(sessions) * CLICKS_PER_DAY * len(STOCKS)
    for symbol in STOCKS:
        for day in sessions:
            for click in deterministic_clicks(day):
                technical = core.technical_at(symbol, histories[symbol], click)
                context = context_at(symbol, click, context_histories, knowledge)
                decision = brain.decide(symbol, technical, context)
                rows.append({
                    "trade_date": day.isoformat(),
                    "click_at": click.isoformat(),
                    "symbol": symbol,
                    "category": categories[symbol],
                    "technical": technical,
                    "context": context,
                    "decision": decision,
                })
    if len(rows) != expected:
        return {"protocol_id": PROTOCOL_ID, "status": "OBSERVATION_COUNT_MISMATCH", "observations": len(rows), "expected": expected, "safety": architecture_contract()}

    frozen_tape = {symbol: [row for row in histories[symbol]["5m"] if (stamp := baseline._stamp(row[0])) and EXPIRY_START <= stamp.astimezone(IST).date() <= DATA_END] for symbol in STOCKS}
    return {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED",
        "experiment": {
            "stocks": [{"symbol": symbol, "category": category} for symbol, category in FROZEN_STOCKS],
            "expiry_cycle_start": EXPIRY_START.isoformat(),
            "expiry_date": EXPIRY_DATE.isoformat(),
            "data_frozen_through": DATA_END.isoformat(),
            "completed_sessions": [day.isoformat() for day in sessions],
            "session_count": len(sessions),
            "clicks_per_session": CLICKS_PER_DAY,
            "observations_per_stock": len(sessions) * CLICKS_PER_DAY,
            "expected_observations": expected,
            "observations": len(rows),
            "same_click_times_across_stocks": True,
            "evaluation_run": False,
        },
        "click_manifest": clicks_manifest,
        "knowledge": knowledge,
        "data_coverage": {
            symbol: {
                tf: {"candles": len(histories[symbol][tf]), "first": str(histories[symbol][tf][0][0]) if histories[symbol][tf] else None, "last": str(histories[symbol][tf][-1][0]) if histories[symbol][tf] else None}
                for tf in TIMEFRAMES
            }
            for symbol in STOCKS
        },
        "context_coverage": {symbol: len(rows_) for symbol, rows_ in sorted(context_histories.items())},
        "frozen_5m_tape_hashes": {symbol: _tape_hash(frozen_tape[symbol]) for symbol in STOCKS},
        "frozen_5m_tape_by_stock": frozen_tape,
        "rows": rows,
        "safety": architecture_contract(),
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "brain_protocol_id": brain.PROTOCOL_ID,
        "stocks_frozen": [symbol for symbol, _ in FROZEN_STOCKS],
        "completed_candles_only": True,
        "point_in_time_context_only": True,
        "unknown_news_time_policy": "NEXT_NSE_SESSION_OPEN",
        "random_clicks_per_session": CLICKS_PER_DAY,
        "clicks_deterministic_and_frozen": True,
        "future_tape_frozen": True,
        "future_outcomes_resolved": False,
        "evaluation_metrics_computed": False,
        "options_read_for_decision": False,
        "futures_read_for_decision": False,
        "v3_thresholds_changed": False,
        "live_execution": False,
        "capital_committed": 0,
    }
