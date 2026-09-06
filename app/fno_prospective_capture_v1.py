"""Bounded prospective F&O episode capture built on Market Brain V2."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from .external_context import external_market_context
from .fno_market_brain_v2 import build_experience_memory, build_perception, decide_shadow
from .fno_prospective_protocol_v1 import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    MIN_RISK_REWARD,
    PRIMARY_HORIZON_MINUTES,
    PROTOCOL_ID,
    TIMEFRAMES,
    session_outcome_eligible,
)
from .fno_prospective_store_v1 import FnoProspectiveStore
from .fno_selected_contract_tape_v1 import build_selected_observation
from .providers.groww import GrowwProvider

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_UNIVERSE = tuple(sorted(set(GrowwProvider.NSE_CASH_SYMBOLS) | {"NIFTY", "BANKNIFTY"}))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("prospective F&O capture timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def capture_slot(at: datetime) -> datetime:
    local = _utc(at).astimezone(IST)
    minute = (local.minute // 15) * 15
    return local.replace(minute=minute, second=0, microsecond=0).astimezone(timezone.utc)


def _continuous_session(at: datetime) -> bool:
    local = _utc(at).astimezone(IST)
    minutes = local.hour * 60 + local.minute
    return local.weekday() < 5 and 9 * 60 + 15 <= minutes < 15 * 60 + 15


def deterministic_batch(
    at: datetime,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    universe: Iterable[str] | None = None,
) -> list[str]:
    items = sorted({str(item).strip().upper() for item in (universe or DEFAULT_UNIVERSE) if str(item).strip()})
    if not items:
        return []
    size = max(1, min(int(batch_size), MAX_BATCH_SIZE, len(items)))
    slot = capture_slot(at)
    slot_number = int(slot.timestamp() // (15 * 60))
    start = (slot_number * size) % len(items)
    return [items[(start + index) % len(items)] for index in range(size)]


def _technical_map(scan: dict, symbols: list[str]) -> dict[str, dict]:
    rows = list(scan.get("setups") or []) + list(scan.get("others") or [])
    result = {str(row.get("symbol") or "").upper(): row for row in rows if isinstance(row, dict)}
    return {symbol: result.get(symbol, {"symbol": symbol, "status": "NO_TRADE"}) for symbol in symbols}


def _episode_id(symbol: str, slot_at: datetime) -> str:
    stable = f"{PROTOCOL_ID}|{symbol.upper()}|{_utc(slot_at).isoformat()}"
    return "fnoep-" + hashlib.sha256(stable.encode()).hexdigest()[:28]


async def capture_prospective_batch(
    provider,
    store: FnoProspectiveStore,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    symbols: Iterable[str] | None = None,
) -> dict:
    now = _utc(now or datetime.now(timezone.utc))
    if not _continuous_session(now):
        return {
            "status": "SKIPPED_OUTSIDE_CONTINUOUS_SESSION",
            "captured_at": now.isoformat(),
            "symbols": [],
            "live_execution": False,
            "capital_committed": 0,
        }

    selected = deterministic_batch(now, batch_size=batch_size, universe=symbols)
    if not selected:
        return {
            "status": "NO_SYMBOLS",
            "captured_at": now.isoformat(),
            "symbols": [],
            "live_execution": False,
            "capital_committed": 0,
        }

    slot_at = capture_slot(now)
    scan = await provider.multi_timeframe_scan(selected, list(TIMEFRAMES), MIN_RISK_REWARD)
    technical_by_symbol = _technical_map(scan, selected)
    try:
        market = await provider.market_context(list(TIMEFRAMES))
    except Exception as exc:
        market = {"status": "UNAVAILABLE", "error": f"{exc.__class__.__name__}: {str(exc)[:180]}"}

    captured = []
    failures = []
    for symbol in selected:
        try:
            technical = technical_by_symbol[symbol]
            chain = await provider.option_chain(symbol)
            try:
                external = await external_market_context(symbol)
            except Exception as exc:
                external = {
                    "status": "UNAVAILABLE",
                    "error": f"{exc.__class__.__name__}: {str(exc)[:180]}",
                }
            decision_at = datetime.now(timezone.utc)
            snapshot = {
                "provider": chain.get("provider") or "GROWW",
                "underlying_symbol": symbol,
                "expiry_date": chain.get("expiry"),
                "observed_at": decision_at,
                "payload": chain,
            }
            perception = build_perception(
                snapshot,
                decision_at=decision_at,
                technical=technical,
                external_context={"market_context": market, "external": external},
            )
            prior = await store.prior_cases(
                datetime.fromisoformat(perception["observed_at"].replace("Z", "+00:00")),
                limit=1000,
            )
            memory = build_experience_memory(perception, prior, limit=5)
            decision = decide_shadow(perception, memory, max_snapshot_age_seconds=120)

            due = decision_at + timedelta(minutes=PRIMARY_HORIZON_MINUTES)
            eligible = session_outcome_eligible(decision_at)
            episode_id = _episode_id(symbol, slot_at)
            record = {
                "protocol_id": PROTOCOL_ID,
                "episode_id": episode_id,
                "capture_slot_at": slot_at.isoformat(),
                "captured_at": decision_at.isoformat(),
                "decision_at": decision_at.isoformat(),
                "outcome_due_at": due.isoformat(),
                "outcome_eligible": eligible,
                "perception": perception,
                "memory": memory,
                "decision": decision,
                "future_outcome_present_in_decision": False,
                "outcome_used_for_decision": False,
                "futures_trade_generated": False,
                "live_execution": False,
                "capital_committed": 0,
            }
            stored = await store.insert_episode(record)
            initial_observation = None
            if stored.get("status") == "INSERTED" and decision.get("research_action") in {"BUY_CE", "BUY_PE"}:
                candidate = decision.get("research_candidate") or {}
                episode_contract = {
                    "episode_id": episode_id,
                    "underlying_symbol": symbol,
                    "expiry_date": perception["source"].get("expiry_date"),
                    "trading_symbol": candidate.get("trading_symbol"),
                    "strike": candidate.get("strike"),
                    "option_type": candidate.get("option_type"),
                }
                observation = build_selected_observation(
                    episode_contract,
                    chain,
                    collected_at=decision_at,
                    direct_quote={"status": "NOT_ATTEMPTED_INITIAL_FREEZE"},
                )
                initial_observation = await store.insert_observation(observation)

            captured.append({
                "symbol": symbol,
                "episode_id": stored.get("episode_id") or episode_id,
                "store_status": stored.get("status"),
                "research_action": decision.get("research_action"),
                "outcome_eligible": eligible,
                "memory_status": memory.get("status"),
                "initial_selected_observation": initial_observation,
            })
        except Exception as exc:
            failures.append({
                "symbol": symbol,
                "error": f"{exc.__class__.__name__}: {str(exc)[:300]}",
            })

    return {
        "status": "CAPTURED" if not failures else "PARTIAL" if captured else "FAILED",
        "protocol_id": PROTOCOL_ID,
        "capture_slot_at": slot_at.isoformat(),
        "selected_symbols": selected,
        "captured": captured,
        "failed": failures,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generated": False,
        "strategy_policy_changed": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "FNO_PROSPECTIVE_CAPTURE_V1",
        "bounded_batch": True,
        "deterministic_sampling": True,
        "captures_no_trade_episodes": True,
        "decision_frozen_before_outcome": True,
        "strictly_prior_memory": True,
        "memory_decision_effect": "DESCRIPTIVE_ONLY",
        "future_outcomes_used": False,
        "threshold_tuning": False,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generation": False,
    }
