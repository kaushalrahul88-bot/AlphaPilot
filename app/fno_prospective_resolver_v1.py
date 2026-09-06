"""Prospective F&O outcome resolver with strict decision/outcome separation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .fno_prospective_protocol_v1 import MAX_RESOLUTIONS_PER_PASS, PRIMARY_HORIZON_MINUTES, PROTOCOL_ID
from .fno_prospective_store_v1 import FnoProspectiveStore


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _stamp(value: Any) -> datetime | None:
    try:
        if isinstance(value, datetime):
            stamp = value
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        return stamp.astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_candles(raw: list, decision_at: datetime, due_at: datetime) -> list[dict]:
    result = []
    for row in raw or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        stamp = _stamp(row[0])
        if stamp is None or stamp < decision_at or stamp > due_at:
            continue
        try:
            o, h, l, c = [float(value) for value in row[1:5]]
        except (TypeError, ValueError):
            continue
        if min(o, h, l, c) <= 0 or h < l:
            continue
        result.append({"at": stamp, "open": o, "high": h, "low": l, "close": c})
    result.sort(key=lambda item: item["at"])
    return result


def _pct(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base <= 0:
        return None
    return round((value / base - 1.0) * 100.0, 6)


def _resolution_fingerprint(record: dict) -> str:
    core = {
        key: record.get(key)
        for key in (
            "episode_id", "horizon_minutes", "outcome_due_at", "resolved_at",
            "resolution_status", "classification", "underlying_return_pct",
            "option_return_pct", "option_observations",
        )
    }
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def build_outcome(
    episode: dict,
    underlying_candles: list,
    selected_observations: list[dict],
    *,
    resolved_at: datetime,
) -> dict:
    resolved_at = resolved_at.astimezone(timezone.utc)
    decision_at = episode["decision_at"].astimezone(timezone.utc)
    due_at = episode["outcome_due_at"].astimezone(timezone.utc)
    frozen = episode.get("payload") or {}
    perception = frozen.get("perception") or {}
    start_price = _number((perception.get("underlying") or {}).get("ltp"))
    action = str(episode.get("research_action") or "NO_TRADE").upper()

    status = "RESOLVED"
    classification = "NO_TRADE_OBSERVED" if action == "NO_TRADE" else "OPTION_FLAT"
    candles = _normalize_candles(underlying_candles, decision_at, due_at)

    underlying_end = candles[-1]["close"] if candles else None
    max_high = max((item["high"] for item in candles), default=None)
    min_low = min((item["low"] for item in candles), default=None)
    underlying_return = _pct(underlying_end, start_price)
    max_up = _pct(max_high, start_price)
    max_down = _pct(min_low, start_price)

    if not episode.get("outcome_eligible"):
        status = "INELIGIBLE_LATE_SESSION_HORIZON"
        classification = "NOT_ADMITTED_TO_MEMORY"
    elif start_price is None or not candles or underlying_end is None:
        status = "UNDERLYING_DATA_INCOMPLETE"
        classification = "NOT_ADMITTED_TO_MEMORY"

    usable_options = [
        item for item in selected_observations
        if item.get("observed_at") <= due_at and (_number(item.get("ltp")) or 0) > 0
    ]
    usable_options.sort(key=lambda item: item["observed_at"])
    option_start = _number(episode.get("selected_reference_ltp"))
    option_end = _number(usable_options[-1].get("ltp")) if usable_options else None
    option_prices = [_number(item.get("ltp")) for item in usable_options]
    option_prices = [value for value in option_prices if value is not None and value > 0]
    option_return = _pct(option_end, option_start)

    if status == "RESOLVED" and action in {"BUY_CE", "BUY_PE"}:
        # Initial freeze plus at least one genuinely later first-seen observation.
        if option_start is None or len(usable_options) < 2 or option_end is None:
            status = "SELECTED_OPTION_TAPE_INCOMPLETE"
            classification = "NOT_ADMITTED_TO_MEMORY"
        elif option_return is not None:
            if option_return > 0.25:
                classification = "OPTION_GAIN"
            elif option_return < -0.25:
                classification = "OPTION_LOSS"
            else:
                classification = "OPTION_FLAT"

    record = {
        "protocol_id": PROTOCOL_ID,
        "episode_id": episode["episode_id"],
        "horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "outcome_due_at": due_at.isoformat(),
        "resolved_at": resolved_at.isoformat(),
        # The outcome is knowable to future Memory only once this resolver actually runs.
        "available_at": resolved_at.isoformat(),
        "resolution_status": status,
        "classification": classification,
        "underlying_start_price": start_price,
        "underlying_end_price": underlying_end,
        "underlying_return_pct": underlying_return,
        "max_up_pct": max_up,
        "max_down_pct": max_down,
        "option_observations": len(usable_options),
        "option_end_ltp": option_end,
        "option_return_pct": option_return,
        "option_max_ltp": max(option_prices) if option_prices else None,
        "option_min_ltp": min(option_prices) if option_prices else None,
        "decision_rewritten": False,
        "outcome_used_for_decision": False,
        "memory_admission_eligible": status == "RESOLVED",
        "options_outcome_basis": "FIRST_SEEN_LIVE_SELECTED_CONTRACT_TAPE_ONLY",
        "underlying_outcome_basis": "GROWW_RECONSTRUCTIBLE_HISTORICAL_5M_FETCHED_AFTER_HORIZON",
        "historical_option_chain_backfill_used": False,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generated": False,
    }
    record["resolution_fingerprint"] = _resolution_fingerprint(record)
    return record


async def resolve_due_fno_episodes(
    provider,
    store: FnoProspectiveStore,
    *,
    now: datetime | None = None,
    limit: int = MAX_RESOLUTIONS_PER_PASS,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    due = await store.due_episodes(now, limit=limit)
    resolved = []
    failed = []

    for episode in due:
        try:
            candles = []
            if episode.get("outcome_eligible"):
                candles = await provider.candles(episode["underlying_symbol"], "5m")
            observations = await store.observations_for_episode(
                episode["episode_id"], episode["outcome_due_at"]
            )
            outcome = build_outcome(
                episode,
                candles,
                observations,
                resolved_at=now,
            )
            stored = await store.insert_outcome(outcome)
            resolved.append({
                "episode_id": episode["episode_id"],
                "research_action": episode.get("research_action"),
                "resolution_status": outcome["resolution_status"],
                "classification": outcome["classification"],
                "memory_admission_eligible": outcome["memory_admission_eligible"],
                "store_status": stored.get("status"),
            })
        except Exception as exc:
            failed.append({
                "episode_id": episode.get("episode_id"),
                "error": f"{exc.__class__.__name__}: {str(exc)[:300]}",
            })

    return {
        "status": "RESOLVED" if not failed else "PARTIAL" if resolved else "FAILED",
        "due_episodes": len(due),
        "resolved": resolved,
        "failed": failed,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generated": False,
        "decision_rewritten": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "FNO_PROSPECTIVE_RESOLVER_V1",
        "resolves_only_after_due": True,
        "decision_rewritten": False,
        "outcome_used_for_original_decision": False,
        "no_trade_outcome_allowed_from_underlying_only": True,
        "actionable_requires_first_seen_selected_option_tape": True,
        "incomplete_actionable_outcome_admitted_to_memory": False,
        "historical_option_chain_backfill": False,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generation": False,
    }
