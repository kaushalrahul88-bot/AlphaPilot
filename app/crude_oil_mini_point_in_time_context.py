from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .commodity_time import parse_ist_timestamp


@dataclass(frozen=True)
class PointInTimeContextRecord:
    series: str
    observed_at: str
    available_at: str
    source: str
    value: Any
    quality: str = "OBSERVED"
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


SERIES_POLICY = {
    "MCX_CRUDEOILM": {"role": "PRIMARY_UNDERLYING", "required": True},
    "WTI_CRUDE": {"role": "GLOBAL_CRUDE_REFERENCE", "required": False},
    "BRENT_CRUDE": {"role": "GLOBAL_CRUDE_REFERENCE", "required": False},
    "USDINR": {"role": "MCX_CURRENCY_TRANSLATION", "required": False},
    "DXY": {"role": "USD_REGIME", "required": False},
    "EIA_CRUDE_INVENTORY": {"role": "SCHEDULED_FUNDAMENTAL_EVENT", "required": False},
    "OPEC_SUPPLY": {"role": "SUPPLY_POLICY_CONTEXT", "required": False},
    "CRUDE_NEWS": {"role": "EVENT_CONTEXT", "required": False},
    "MCX_CRUDEOILM_OPTION": {"role": "OPTION_TRANSLATION", "required": False},
}


def visible_at(records, click_timestamp) -> list[dict]:
    """Return only records genuinely observed and available by the simulated click."""
    click = parse_ist_timestamp(click_timestamp)
    visible = []
    for record in records or []:
        try:
            observed = parse_ist_timestamp(record["observed_at"])
            available = parse_ist_timestamp(record["available_at"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if observed <= click and available <= click:
            visible.append(record)
    return sorted(visible, key=lambda row: (str(row.get("series") or ""), row["observed_at"], row["available_at"]))


def latest_known_as_of(records, click_timestamp, max_age_seconds=None) -> dict[str, dict]:
    """Return the latest genuinely available observation per Crude context series."""
    click = parse_ist_timestamp(click_timestamp)
    latest: dict[str, dict] = {}
    for record in visible_at(records, click_timestamp):
        series = str(record.get("series") or "")
        if not series:
            continue
        available = parse_ist_timestamp(record["available_at"])
        age = max(0.0, (click - available).total_seconds())
        if max_age_seconds is not None and age > float(max_age_seconds):
            continue
        current = latest.get(series)
        if current is None or parse_ist_timestamp(current["available_at"]) <= available:
            enriched = dict(record)
            enriched["age_seconds"] = round(age, 3)
            latest[series] = enriched
    return latest


def audit_context_coverage(records, click_timestamps) -> dict:
    rows = []
    required = {series for series, policy in SERIES_POLICY.items() if policy.get("required")}
    for timestamp in click_timestamps or []:
        latest = latest_known_as_of(records, timestamp)
        series = set(latest)
        rows.append({
            "click_timestamp": timestamp,
            "visible_series": sorted(series),
            "missing_required_series": sorted(required - series),
            "missing_optional_series": sorted(set(SERIES_POLICY) - series - required),
            "required_context_complete": required.issubset(series),
        })
    return {
        "mode": "CRUDE_OIL_MINI_POINT_IN_TIME_CONTEXT_COVERAGE_V1",
        "product": "CRUDE_OIL_MINI",
        "lookahead_guard": "observed_at <= click AND available_at <= click",
        "selection_semantics": "Latest genuinely available observation per series; missing historical context remains missing.",
        "series_policy": SERIES_POLICY,
        "clicks": rows,
    }


def acquisition_manifest() -> dict:
    """Describe the Crude-specific context needed to reach Copper-framework parity.

    This is a source/readiness contract only. It deliberately does not fabricate
    unavailable historical intraday context and does not enable any context lane.
    """
    return {
        "mode": "CRUDE_OIL_MINI_CONTEXT_ACQUISITION_MANIFEST_V1",
        "product": "CRUDE_OIL_MINI",
        "principle": "Unavailable historical context stays unavailable; never backfill a simulated click with later information.",
        "feeds": [
            {
                "series": "MCX_CRUDEOILM",
                "status": "AVAILABLE_INTERNAL",
                "source": "Groww exact current-listed CRUDEOILM futures history",
                "requirement": "5-minute bars with bar-start timestamps; OHLC visible only after bar completion.",
            },
            {
                "series": "WTI_CRUDE",
                "status": "SOURCE_TO_VALIDATE",
                "requirement": "Timestamped intraday front-month WTI observations available at each simulated click.",
            },
            {
                "series": "BRENT_CRUDE",
                "status": "SOURCE_TO_VALIDATE",
                "requirement": "Timestamped intraday Brent observations available at each simulated click.",
            },
            {
                "series": "USDINR",
                "status": "INTRADAY_SOURCE_REQUIRED",
                "requirement": "Timestamped intraday observations; daily reference rates cannot stand in for click-time FX.",
            },
            {
                "series": "DXY",
                "status": "INTRADAY_SOURCE_REQUIRED",
                "requirement": "Timestamped intraday observations; daily close data is context-only for intraday replay.",
            },
            {
                "series": "EIA_CRUDE_INVENTORY",
                "status": "PIT_RELEASE_ARCHIVE_REQUIRED",
                "requirement": "Release timestamp, actual, prior and consensus only when the consensus has auditable pre-release provenance.",
            },
            {
                "series": "OPEC_SUPPLY",
                "status": "PIT_RELEASE_ARCHIVE_REQUIRED",
                "requirement": "Publication/release timestamp and revision-safe policy/supply metadata.",
            },
            {
                "series": "CRUDE_NEWS",
                "status": "PIT_NEWS_ARCHIVE_REQUIRED",
                "requirement": "Publication timestamp, source, revision-safe headline/event metadata and duplicate/material-update handling.",
            },
            {
                "series": "MCX_CRUDEOILM_OPTION",
                "status": "COLLECT_FORWARD",
                "requirement": "Actual Mini option chain/snapshots for later Option Brain translation; never synthesize unavailable premium history.",
            },
        ],
        "required_fields": ["series", "observed_at", "available_at", "source", "value", "quality"],
        "current_brain_effect": "NONE",
        "news_enabled": False,
        "option_translation_enabled": False,
    }
