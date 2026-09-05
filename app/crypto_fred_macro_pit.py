"""Immutable first-seen archive adapter for live FRED/ALFRED BTC macro regime.

Historical ALFRED vintage reconstruction is intentionally excluded from this
archive because it is reconstructible public history. Only a snapshot AlphaPilot
actually fetched prospectively may enter the irrecoverable PIT store.

The natural source identity contains a deterministic provider-state hash. Thus an
unchanged later poll is idempotent and preserves earliest first_seen_at, while a
legitimate same-day FRED state change creates a separate immutable observation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture
from app.fred_btc_macro_regime_provider import FredBtcMacroRegimeCapture, FredSeriesVintageChange

DATASET = "FRED_MACRO_REGIME_LIVE_SNAPSHOT"
PROVIDER = "FRED_ALFRED"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _series_payload(row: FredSeriesVintageChange) -> dict:
    row.validated()
    return {
        "series_id": row.series_id,
        "vintage_date": row.vintage_date.isoformat(),
        "latest_observation_date": row.latest_observation_date.isoformat(),
        "previous_observation_date": row.previous_observation_date.isoformat(),
        "latest_value": float(row.latest_value),
        "previous_value": float(row.previous_value),
        "realtime_start": row.realtime_start.isoformat(),
        "realtime_end": row.realtime_end.isoformat(),
        "change_value": float(row.change_value),
        "change_unit": row.change_unit,
    }


def _state_material(capture: FredBtcMacroRegimeCapture) -> dict:
    capture.validated()
    return {
        "vintage_date": capture.vintage_date.isoformat(),
        "broad_usd": _series_payload(capture.broad_usd),
        "real_yield_10y": _series_payload(capture.real_yield_10y),
        "nasdaq_composite": _series_payload(capture.nasdaq_composite),
        "vix": _series_payload(capture.vix),
    }


def provider_state_hash(capture: FredBtcMacroRegimeCapture) -> str:
    material = _state_material(capture)
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(raw).hexdigest()


def fred_macro_live_archive_record(capture: FredBtcMacroRegimeCapture) -> BtcPitArchiveRecord:
    row = capture.validated()
    if row.historical_vintage_reconstruction:
        raise ValueError("historical ALFRED vintage reconstruction must not enter live irrecoverable PIT archive")
    if row.exact_intraday_availability_proven is not True:
        raise ValueError("live FRED macro snapshot requires actual AlphaPilot first-seen availability")

    first_seen = _utc(row.first_seen_at)
    if row.vintage_date != first_seen.date():
        raise ValueError("live FRED macro snapshot vintage_date must match AlphaPilot first_seen calendar date")

    state_hash = provider_state_hash(row)
    payload = {
        **_state_material(row),
        "provider_state_hash": state_hash,
        "provider_event_time_available": False,
        "exact_macro_release_time_proven": False,
        "historical_vintage_reconstruction": False,
        "live_first_seen_snapshot": True,
        "daily_regime_context_only": True,
        "standalone_direction_assigned": False,
        "may_supply_second_intraday_origin": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
    }
    return archive_record_from_capture(
        dataset=DATASET,
        provider=PROVIDER,
        source_key=f"FRED_MACRO_REGIME:{row.vintage_date.isoformat()}:{state_hash}",
        first_seen_at=first_seen,
        event_at=None,
        source_version="FRED_ALFRED_BTC_MACRO_REGIME_LIVE_V1",
        payload=payload,
    )


def architecture_contract() -> dict:
    return {
        "version": "FRED_BTC_MACRO_LIVE_PIT_V1",
        "dataset": DATASET,
        "historical_vintage_reconstruction_admitted": False,
        "live_first_seen_required": True,
        "same_day_unchanged_repoll_is_idempotent": True,
        "same_day_changed_provider_state_creates_new_record": True,
        "provider_event_time_claimed": False,
        "exact_macro_release_time_claimed": False,
        "first_seen_controls_click_visibility": True,
        "daily_regime_context_only": True,
        "may_supply_second_intraday_origin": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
