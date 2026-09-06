"""Resolve prospective underlying episodes from exact completed 5-minute paths."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import fno_15m_historical_replay_v1 as technical_core
from .fno_underlying_prospective_v1 import (
    HORIZONS_MINUTES,
    NO_TRADE_LARGE_MOVE_PCT,
    PROTOCOL_ID,
)
from .fno_underlying_random_replay_v1 import _path_metrics

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
LOOKBACK_DAYS = 5
HORIZON_CODES = tuple(f"{value}m" for value in HORIZONS_MINUTES) + ("EOD",)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def outcome_due_at(slot_at: datetime, code: str) -> datetime:
    slot_at = _utc(slot_at)
    if code == "EOD":
        local_date = slot_at.astimezone(IST).date()
        return datetime.combine(
            local_date,
            datetime.min.time().replace(hour=15, minute=30),
            tzinfo=IST,
        ).astimezone(UTC)
    minutes = int(code[:-1])
    if minutes not in HORIZONS_MINUTES:
        raise ValueError("invalid horizon code")
    return slot_at + timedelta(minutes=minutes)


def _exact_future_bars(rows: list[list], slot_at: datetime, due_at: datetime) -> list[list] | None:
    slot_at = _utc(slot_at)
    due_at = _utc(due_at)
    expected = int((due_at - slot_at).total_seconds() // 300)
    if expected <= 0:
        return None
    found: list[tuple[datetime, list]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        stamp = technical_core._stamp(row[0])
        if stamp is None:
            continue
        if slot_at <= stamp and stamp + timedelta(minutes=5) <= due_at:
            found.append((stamp, list(row)))
    found.sort(key=lambda item: item[0])
    if len(found) != expected:
        return None
    if found[0][0] != slot_at or found[-1][0] + timedelta(minutes=5) != due_at:
        return None
    for index in range(1, len(found)):
        if found[index][0] - found[index - 1][0] != timedelta(minutes=5):
            return None
    return [row for _, row in found]


def _classification(action: str, metrics: dict[str, Any]) -> str:
    if not metrics.get("resolved"):
        return "UNRESOLVED"
    if action in {"LONG", "SHORT"}:
        directional = metrics.get("directional_return_pct")
        if directional is None:
            return "UNRESOLVED"
        if directional > 0:
            return "DIRECTION_CORRECT"
        if directional < 0:
            return "DIRECTION_INCORRECT"
        return "FLAT"
    excursion = float(metrics.get("max_abs_excursion_pct") or 0)
    return "NO_TRADE_LARGE_MOVE" if excursion >= NO_TRADE_LARGE_MOVE_PCT else "NO_TRADE_QUIET"


async def resolve_due_underlying_outcomes(provider, store, *, now: datetime | None = None, limit: int = 500) -> dict:
    now = _utc(now or datetime.now(UTC))
    pending = await store.pending_episodes(now - timedelta(days=LOOKBACK_DAYS), limit=limit)
    due_items: list[tuple[dict, str, datetime]] = []
    for episode in pending:
        resolved = set(episode.get("resolved_horizons") or set())
        for code in HORIZON_CODES:
            if code in resolved:
                continue
            due = outcome_due_at(episode["capture_slot_at"], code)
            if now >= due:
                due_items.append((episode, code, due))

    if not due_items:
        return {
            "status": "NOTHING_DUE",
            "protocol_id": PROTOCOL_ID,
            "resolved": 0,
            "live_execution": False,
            "capital_committed": 0,
        }

    histories: dict[str, list[list]] = {}
    fetch_errors: dict[str, str] = {}
    for symbol in sorted({item[0]["underlying_symbol"] for item in due_items}):
        try:
            histories[symbol] = list(await provider.candles(symbol, "5m") or [])
        except Exception as exc:
            fetch_errors[symbol] = f"{exc.__class__.__name__}: {str(exc)[:240]}"

    inserted = []
    incomplete = []
    for episode, code, due in due_items:
        symbol = episode["underlying_symbol"]
        if symbol in fetch_errors:
            incomplete.append({
                "episode_id": episode["episode_id"],
                "horizon_code": code,
                "reason": "HISTORY_FETCH_FAILED",
                "error": fetch_errors[symbol],
            })
            continue
        full_rows = histories.get(symbol, [])
        exact = _exact_future_bars(full_rows, episode["capture_slot_at"], due)
        if exact is None:
            incomplete.append({
                "episode_id": episode["episode_id"],
                "horizon_code": code,
                "reason": "EXACT_5M_PATH_NOT_YET_COMPLETE",
            })
            continue
        action = str(episode["research_action"]).upper()
        direction = action if action in {"LONG", "SHORT"} else None
        metrics = _path_metrics(
            exact,
            episode["capture_slot_at"],
            due,
            float(episode["reference_price"]),
            direction,
        )
        if not metrics.get("resolved"):
            incomplete.append({
                "episode_id": episode["episode_id"],
                "horizon_code": code,
                "reason": "PATH_METRICS_UNRESOLVED",
            })
            continue
        record = {
            "protocol_id": PROTOCOL_ID,
            "episode_id": episode["episode_id"],
            "underlying_symbol": symbol,
            "research_action": action,
            "capture_slot_at": episode["capture_slot_at"].isoformat(),
            "reference_price": episode["reference_price"],
            "horizon_code": code,
            "outcome_due_at": due.isoformat(),
            "resolved_at": now.isoformat(),
            "resolution_status": "EXACT_COMPLETED_5M_PATH",
            "classification": _classification(action, metrics),
            "end_price": metrics.get("end_price"),
            "raw_return_pct": metrics.get("raw_return_pct"),
            "directional_return_pct": metrics.get("directional_return_pct"),
            "max_up_pct": metrics.get("max_up_pct"),
            "max_down_pct": metrics.get("max_down_pct"),
            "mfe_pct": metrics.get("mfe_pct"),
            "mae_pct": metrics.get("mae_pct"),
            "max_abs_excursion_pct": metrics.get("max_abs_excursion_pct"),
            "option_data_used": False,
            "futures_data_used": False,
            "outcome_used_for_decision": False,
        }
        stored = await store.insert_outcome(record)
        inserted.append({
            "episode_id": episode["episode_id"],
            "symbol": symbol,
            "horizon_code": code,
            "classification": record["classification"],
            "store_status": stored.get("status"),
        })

    return {
        "status": "RESOLVED" if inserted else "PENDING_EXACT_PATH",
        "protocol_id": PROTOCOL_ID,
        "due": len(due_items),
        "resolved": len(inserted),
        "incomplete": incomplete,
        "history_fetch_errors": fetch_errors,
        "outcomes": inserted,
        "live_execution": False,
        "capital_committed": 0,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_UNDERLYING_PROSPECTIVE_RESOLVER_V1",
        "exact_5m_path_required": True,
        "horizons": list(HORIZON_CODES),
        "partial_paths_persisted": False,
        "option_data_used": False,
        "futures_data_used": False,
        "outcomes_can_change_decisions": False,
        "live_execution": False,
        "capital_committed": 0,
    }
