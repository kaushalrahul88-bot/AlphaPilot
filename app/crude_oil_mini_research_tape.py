from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_candle_collector import _records
from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_contracts import CRUDE_OIL_MINI, fetch_crude_oil_mini_master, resolve_crude_oil_mini_universe
from .crude_oil_mini_data_probe import _complete_sessions


IST = ZoneInfo("Asia/Kolkata")
TIMEFRAME_MINUTES = 5
FROZEN_CURRENT_CONTRACT = "CRUDEOILM21SEP26FUT"
FROZEN_RESEARCH_START = datetime(2026, 6, 1, 0, 0, tzinfo=IST)
FROZEN_RESEARCH_END = datetime(2026, 8, 31, 23, 30, tzinfo=IST)
CHUNK_DAYS = 7

# Canonical snapshot sealed by the successful 1,080-click framework audit.
# These values are data-integrity identifiers, not strategy thresholds.
FROZEN_TAPE_SHA256 = "a7379844a91771af2b386a452868713bdee73c70311a3f2fd76a29f4db6064bb"
FROZEN_TAPE_CANDLES = 10125
FROZEN_TAPE_FIRST_AT = "2026-06-01T09:15:00+05:30"
FROZEN_TAPE_LAST_AT = "2026-08-31T23:25:00+05:30"
FROZEN_COMPLETE_SESSIONS = 54
FROZEN_COMPLETE_SESSION_FIRST = "2026-06-16"
FROZEN_COMPLETE_SESSION_LAST = "2026-08-31"


def _storage_contract(contract: dict) -> dict:
    out = dict(contract or {})
    out["underlying"] = CRUDE_OIL_MINI
    out["exchange"] = "MCX"
    out["segment"] = "COMMODITY"
    out["expiry_date"] = out.get("expiry_date") or out.get("expiry")
    return out


def _completed_end(value: datetime) -> datetime:
    observed = parse_ist_timestamp(value).astimezone(IST)
    minute_of_day = observed.hour * 60 + observed.minute
    floored = (minute_of_day // TIMEFRAME_MINUTES) * TIMEFRAME_MINUTES
    aligned = observed.replace(
        hour=floored // 60,
        minute=floored % 60,
        second=0,
        microsecond=0,
    )
    return aligned - timedelta(minutes=TIMEFRAME_MINUTES)


async def _frozen_contract() -> dict:
    rows = await fetch_crude_oil_mini_master()
    universe = resolve_crude_oil_mini_universe(rows, FROZEN_RESEARCH_END)
    future = dict(universe["future"])
    if str(future.get("trading_symbol") or "").upper() != FROZEN_CURRENT_CONTRACT:
        raise RuntimeError(
            f"Frozen Crude research expects {FROZEN_CURRENT_CONTRACT}; "
            f"instrument master resolved {future.get('trading_symbol')}"
        )
    return _storage_contract(future)


async def _fetch_exact_range(provider, contract: dict, start: datetime, end: datetime) -> list[list]:
    """Fetch one deterministic exact-contract range with PR304 fail-closed semantics.

    Both Groww history routes must succeed for every CRUDEOILM futures chunk. The
    provider's dedicated Mini merger is reused so modern/legacy discrepancies do
    not silently create different research tapes.
    """
    if not hasattr(provider, "_mini_fetch_chunk"):
        raise RuntimeError("Configured provider does not expose the certified CRUDEOILM history path")
    cursor = parse_ist_timestamp(start).astimezone(IST)
    end_at = parse_ist_timestamp(end).astimezone(IST)
    step = timedelta(minutes=TIMEFRAME_MINUTES)
    dedup: dict[str, list] = {}
    while cursor <= end_at:
        chunk_end = min(end_at, cursor + timedelta(days=CHUNK_DAYS) - step)
        chunk = await provider._mini_fetch_chunk(
            contract,
            candle_interval="5minute",
            legacy_minutes=TIMEFRAME_MINUTES,
            start=cursor,
            end=chunk_end,
        )
        for row in chunk or []:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            try:
                stamp = parse_ist_timestamp(row[0]).astimezone(IST)
            except (TypeError, ValueError, OverflowError):
                continue
            if cursor <= stamp <= chunk_end:
                normalized = list(row)
                normalized[0] = stamp.isoformat()
                dedup[stamp.isoformat()] = normalized
        if chunk_end >= end_at:
            break
        cursor = chunk_end + step
    return [dedup[key] for key in sorted(dedup)]


def _matches_canonical_frozen_tape(report: dict) -> bool:
    """Require an exact match to the already-sealed June-August Crude tape."""
    return bool(report) and all((
        report.get("status") == "CERTIFIED",
        report.get("reference_contract") == FROZEN_CURRENT_CONTRACT,
        report.get("tape_sha256") == FROZEN_TAPE_SHA256,
        int(report.get("candles") or 0) == FROZEN_TAPE_CANDLES,
        report.get("first_at") == FROZEN_TAPE_FIRST_AT,
        report.get("last_at") == FROZEN_TAPE_LAST_AT,
        int(report.get("complete_sessions") or 0) == FROZEN_COMPLETE_SESSIONS,
        report.get("complete_session_first") == FROZEN_COMPLETE_SESSION_FIRST,
        report.get("complete_session_last") == FROZEN_COMPLETE_SESSION_LAST,
    ))


async def _reuse_canonical_frozen_tape(store) -> dict | None:
    """Return the sealed snapshot without touching Groww when it already exists."""
    try:
        report = await certify_frozen_research_tape(store, refreshed_rows=0)
    except (RuntimeError, ValueError):
        return None
    if not _matches_canonical_frozen_tape(report):
        return None
    report["refresh"] = {
        "mode": "REUSED_CANONICAL_FROZEN_TAPE",
        "provider_called": False,
        "canonical_sha256": FROZEN_TAPE_SHA256,
        "reason": "Stored tape exactly matches the sealed 1,080-click research snapshot.",
    }
    return report


async def refresh_frozen_research_tape(provider, store, *, now: datetime | None = None) -> dict:
    """Return the sealed research tape, recovering from Groww only when necessary.

    The June 1-Aug 31 dataset is already frozen and fingerprinted. Replays must
    therefore read and certify the stored snapshot first. Groww is contacted only
    when that exact snapshot is absent or fails the canonical fingerprint, avoiding
    both accidental tape drift and unnecessary provider/token-rate-limit exposure.
    """
    await store.initialize()
    reused = await _reuse_canonical_frozen_tape(store)
    if reused is not None:
        return reused

    observed = parse_ist_timestamp(now or datetime.now(IST)).astimezone(IST)
    contract = await _frozen_contract()
    latest = await store.latest_candle_at(FROZEN_CURRENT_CONTRACT, TIMEFRAME_MINUTES)
    start = FROZEN_RESEARCH_START
    if latest is not None:
        latest_at = parse_ist_timestamp(latest).astimezone(IST)
        # Re-read two completed bars to make recovery idempotent and allow an
        # incomplete/corrupt tail to be repaired without refetching the full window.
        start = max(FROZEN_RESEARCH_START, latest_at - timedelta(minutes=10))
    end = min(FROZEN_RESEARCH_END, _completed_end(observed))
    if end < start:
        report = await certify_frozen_research_tape(store, contract=contract, refreshed_rows=0)
        report["refresh"] = {
            "mode": "RECOVERY_NOT_REQUIRED_BY_TIME",
            "provider_called": False,
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
        }
        return report

    fetched = await _fetch_exact_range(provider, contract, start, end)
    collected_at = observed
    records = _records(CRUDE_OIL_MINI, contract, TIMEFRAME_MINUTES, fetched, collected_at)
    upserted = await store.upsert(records)
    report = await certify_frozen_research_tape(store, contract=contract, refreshed_rows=upserted)
    report["refresh"] = {
        "mode": "PROVIDER_RECOVERY",
        "provider_called": True,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "fetched": len(fetched),
        "upserted": upserted,
        "initial_build": latest is None,
        "source_policy": "EXACT_CRUDEOILM_MODERN_PLUS_LEGACY_FAIL_CLOSED",
        "canonical_match_after_recovery": _matches_canonical_frozen_tape(report),
    }
    return report


async def read_frozen_research_tape(store, *, end: datetime | None = None) -> tuple[dict, list[list]]:
    await store.initialize()
    end_at = min(parse_ist_timestamp(end or FROZEN_RESEARCH_END).astimezone(IST), FROZEN_RESEARCH_END)
    segments = await store.read_symbol_contract_segments(
        CRUDE_OIL_MINI,
        TIMEFRAME_MINUTES,
        FROZEN_RESEARCH_START,
        end_at,
    )
    exact = [
        segment for segment in segments
        if str(segment.get("trading_symbol") or "").upper() == FROZEN_CURRENT_CONTRACT
    ]
    if len(exact) != 1:
        raise RuntimeError(
            f"Frozen CRUDEOILM tape requires exactly one {FROZEN_CURRENT_CONTRACT} segment; found {len(exact)}"
        )
    contract = {
        "trading_symbol": FROZEN_CURRENT_CONTRACT,
        "expiry_date": exact[0].get("expiry_date"),
        "underlying": CRUDE_OIL_MINI,
        "exchange": "MCX",
        "segment": "COMMODITY",
    }
    return contract, list(exact[0].get("candles") or [])


def _fingerprint(candles: list[list]) -> str:
    canonical = []
    for row in candles:
        canonical.append([
            parse_ist_timestamp(row[0]).astimezone(IST).isoformat(),
            *[None if value is None else float(value) for value in row[1:7]],
        ])
    payload = json.dumps(canonical, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def certify_frozen_research_tape(store, *, contract: dict | None = None, refreshed_rows: int = 0) -> dict:
    stored_contract, candles = await read_frozen_research_tape(store)
    timestamps = [parse_ist_timestamp(row[0]).astimezone(IST) for row in candles]
    duplicate_timestamps = len(timestamps) - len(set(timestamps))
    non_monotonic_pairs = sum(b <= a for a, b in zip(timestamps, timestamps[1:]))
    off_grid_bars = sum((stamp.minute % TIMEFRAME_MINUTES) != 0 or stamp.second != 0 for stamp in timestamps)
    ohlcv_errors = 0
    for row in candles:
        try:
            o, h, l, c = map(float, row[1:5])
            volume = float(row[5] or 0) if len(row) > 5 else 0.0
            if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h) or volume < 0:
                ohlcv_errors += 1
        except (TypeError, ValueError):
            ohlcv_errors += 1
    sessions = _complete_sessions(candles)
    complete = [row for row in sessions if row.get("complete_for_20_click_research")]
    passed = bool(candles) and all(
        value == 0
        for value in (duplicate_timestamps, non_monotonic_pairs, off_grid_bars, ohlcv_errors)
    ) and bool(complete)
    source_contract = _storage_contract(contract or stored_contract)
    return {
        "mode": "CRUDE_OIL_MINI_FROZEN_RESEARCH_TAPE_V1",
        "status": "CERTIFIED" if passed else "REJECTED",
        "research_only": True,
        "product": "CRUDE_OIL_MINI",
        "underlying_symbol": CRUDE_OIL_MINI,
        "reference_contract": FROZEN_CURRENT_CONTRACT,
        "contract_expiry": source_contract.get("expiry_date"),
        "window": {
            "start": FROZEN_RESEARCH_START.isoformat(),
            "end": FROZEN_RESEARCH_END.isoformat(),
        },
        "candles": len(candles),
        "first_at": timestamps[0].isoformat() if timestamps else None,
        "last_at": timestamps[-1].isoformat() if timestamps else None,
        "sessions": len(sessions),
        "complete_sessions": len(complete),
        "complete_session_first": complete[0]["date"] if complete else None,
        "complete_session_last": complete[-1]["date"] if complete else None,
        "duplicate_timestamps": duplicate_timestamps,
        "non_monotonic_pairs": non_monotonic_pairs,
        "off_grid_bars": off_grid_bars,
        "ohlcv_errors": ohlcv_errors,
        "bar_timing": "GROWW_5M_TIMESTAMP_IS_BAR_START; VISIBLE_AT_START_PLUS_5M",
        "tape_sha256": _fingerprint(candles) if candles else None,
        "refreshed_rows": int(refreshed_rows),
        "integrity": {
            "exact_current_contract_only": True,
            "regular_crude_used": False,
            "older_mini_futures_stitched": False,
            "copper_data_used": False,
            "news_used": False,
            "option_market_data_used": False,
            "synthetic_option_prices_used": False,
        },
    }
