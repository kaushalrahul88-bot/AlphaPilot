from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp

IST = ZoneInfo("Asia/Kolkata")
ALLOWED_SERIES = {"EIA_CRUDE_INVENTORY", "OPEC_SUPPLY", "CRUDE_MACRO_RELEASE", "CRUDE_NEWS"}


def _utc(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_expectations(event_payload: dict, release_utc: datetime) -> tuple[list[dict], list[dict]]:
    all_rows = []
    usable = []
    for raw in (event_payload or {}).get("expectations") or []:
        if not isinstance(raw, dict):
            continue
        row = deepcopy(raw)
        try:
            available = _utc(row.get("available_at_utc"))
            pre_release = available < release_utc
        except Exception:
            pre_release = False
        row["pre_release_usable"] = pre_release
        all_rows.append(row)
        if pre_release:
            usable.append(row)
    return all_rows, usable


def archive_record_to_context(record: dict) -> dict | None:
    """Convert one timestamp-qualified archive item into a neutral PIT context record.

    This adapter intentionally does not infer event direction, materiality, novelty or
    market reaction. Those are separate shadow-research enrichments and must never be
    reconstructed from the eventual price outcome.
    """
    if not isinstance(record, dict) or not bool(record.get("pit_usable")):
        return None
    series = str(record.get("series") or "").upper()
    if series not in ALLOWED_SERIES:
        return None
    available_at = str(record.get("available_at_ist") or "")
    published_at_utc = str(record.get("published_at_utc") or "")
    try:
        available = parse_ist_timestamp(available_at).astimezone(IST)
        release_utc = _utc(published_at_utc)
    except Exception:
        return None

    payload = deepcopy(record.get("event_payload") or {})
    expectations_all, expectations_pre_release = _clean_expectations(payload, release_utc)
    if payload:
        payload["expectations"] = expectations_all
        payload["expectations_pre_release"] = expectations_pre_release
        payload["pre_release_consensus_available"] = bool(expectations_pre_release)

    value = {
        "event_id": record.get("event_id"),
        "event_type": record.get("event_type"),
        "headline": record.get("headline"),
        "facts": record.get("facts"),
        "mechanism_tags": list(record.get("mechanism_tags") or []),
        "timestamp_quality": record.get("timestamp_quality"),
        "published_at_utc": published_at_utc,
        "event_payload": payload,
        "mechanism_stance": "UNKNOWN",
        "materiality_status": "UNASSESSED",
        "novelty_status": "UNASSESSED",
        "surprise_status": "UNASSESSED",
        "reaction": {
            "direction": "UNKNOWN",
            "confirmed": False,
            "confirmation_sources": [],
        },
        "headline_sentiment_inferred": False,
        "outcome_used_for_enrichment": False,
    }
    return {
        "series": series,
        "observed_at": available.isoformat(),
        "available_at": available.isoformat(),
        "source": record.get("source") or "UNKNOWN",
        "quality": record.get("timestamp_quality") or "UNKNOWN",
        "event_id": record.get("event_id"),
        "event_type": record.get("event_type"),
        "value": value,
        "metadata": {
            "source_url": record.get("source_url"),
            "archive_pit_usable": True,
            "direction_inferred": False,
            "reaction_backfilled": False,
        },
    }


def event_context_records(archive: dict) -> list[dict]:
    """Return every valid archive event; do not collapse multiple events per series."""
    rows = []
    for raw in (archive or {}).get("records") or []:
        row = archive_record_to_context(raw)
        if row:
            rows.append(row)
    return sorted(rows, key=lambda row: (parse_ist_timestamp(row["available_at"]), row.get("event_id") or ""))


def visible_event_context(archive: dict, click_timestamp: str) -> list[dict]:
    """Expose all, not merely the latest, events genuinely available by the click."""
    click = parse_ist_timestamp(click_timestamp)
    return [
        row for row in event_context_records(archive)
        if parse_ist_timestamp(row["observed_at"]) <= click
        and parse_ist_timestamp(row["available_at"]) <= click
    ]


def archive_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_PIT_EVENT_ARCHIVE_ADAPTER_V1",
        "research_only": True,
        "current_mind_effect": "NONE",
        "all_visible_events_preserved": True,
        "latest_per_series_collapse": False,
        "headline_direction_inference": False,
        "reaction_backfill": False,
        "pre_release_expectation_rule": "expectation available_at_utc must be strictly before official release timestamp",
        "at_release_consensus_is_pre_release": False,
        "market_outcome_enrichment_allowed": False,
        "promotion_allowed": False,
    }
