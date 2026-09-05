"""Read-only reporting for prospective Massive/CME feed-availability evidence.

This module summarizes persisted operational availability attempts. It never
calls a market-data provider, never writes to storage, never mutates runtime
configuration, and never promotes live macro confirmation. Qualification remains
manual-review-only even when every observed event meets the latency policy.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any, Iterable

from app.crypto_macro_live_availability_audit import (
    MacroLiveAvailabilityAttempt,
    MacroLiveAvailabilityPolicy,
    MacroLiveAvailabilityQualification,
    qualify_massive_live_availability,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("macro availability report timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


async def _await_result(value):
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(frozen=True)
class MacroLiveAvailabilityEventReport:
    event_key: str
    event_type: str
    release_at: datetime
    attempt_count: int
    earliest_attempt_at: datetime
    latest_attempt_at: datetime
    terminal_status: str
    earliest_in_latency_success_at: datetime | None
    earliest_in_latency_success_seconds: float | None
    minimum_observed_latency_seconds: float
    maximum_observed_latency_seconds: float
    has_in_latency_success: bool
    has_too_late_observation: bool
    has_provider_failure: bool

    def validated(self) -> "MacroLiveAvailabilityEventReport":
        if not self.event_key:
            raise ValueError("event_key is required")
        if self.event_type not in {"CPI", "EMPLOYMENT_SITUATION"}:
            raise ValueError("unsupported event_type")
        release = _utc(self.release_at)
        earliest = _utc(self.earliest_attempt_at)
        latest = _utc(self.latest_attempt_at)
        if earliest < release or latest < earliest:
            raise ValueError("invalid event-report chronology")
        if int(self.attempt_count) < 1:
            raise ValueError("attempt_count must be >= 1")
        if self.terminal_status not in {
            "AVAILABLE_WITHIN_LATENCY",
            "AVAILABLE_TOO_LATE",
            "UNAVAILABLE_OR_PROVIDER_ERROR",
        }:
            raise ValueError("unsupported terminal_status")
        if self.minimum_observed_latency_seconds < 0:
            raise ValueError("minimum_observed_latency_seconds must be >= 0")
        if self.maximum_observed_latency_seconds < self.minimum_observed_latency_seconds:
            raise ValueError("maximum latency cannot be below minimum latency")
        if self.has_in_latency_success:
            if self.earliest_in_latency_success_at is None or self.earliest_in_latency_success_seconds is None:
                raise ValueError("successful event requires earliest in-latency success details")
            if _utc(self.earliest_in_latency_success_at) < earliest:
                raise ValueError("success cannot precede earliest attempt")
            if self.earliest_in_latency_success_seconds < 0:
                raise ValueError("success latency must be nonnegative")
        elif self.earliest_in_latency_success_at is not None or self.earliest_in_latency_success_seconds is not None:
            raise ValueError("unsuccessful event cannot contain success details")
        return self


@dataclass(frozen=True)
class MacroLiveAvailabilityReport:
    generated_at: datetime
    qualification: MacroLiveAvailabilityQualification
    unique_events_observed: int
    successful_event_count: int
    too_late_only_event_count: int
    unavailable_only_event_count: int
    event_type_counts: dict[str, int]
    event_type_success_counts: dict[str, int]
    successful_event_latency_min_seconds: float | None
    successful_event_latency_median_seconds: float | None
    successful_event_latency_max_seconds: float | None
    events: tuple[MacroLiveAvailabilityEventReport, ...]
    manual_review_required: bool
    live_confirmation_enabled: bool = False
    provider_network_called: bool = False
    store_written: bool = False
    runtime_mutated: bool = False
    direction_generated: bool = False
    options_trade_generated: bool = False
    futures_trade_generated: bool = False

    def validated(self) -> "MacroLiveAvailabilityReport":
        _utc(self.generated_at)
        self.qualification.validated()
        if self.unique_events_observed != len(self.events):
            raise ValueError("unique_events_observed must match event reports")
        if self.successful_event_count < 0 or self.successful_event_count > self.unique_events_observed:
            raise ValueError("invalid successful_event_count")
        if self.too_late_only_event_count < 0 or self.unavailable_only_event_count < 0:
            raise ValueError("failure counts must be nonnegative")
        if self.successful_event_count + self.too_late_only_event_count + self.unavailable_only_event_count != self.unique_events_observed:
            raise ValueError("event outcome counts must partition unique events")
        if self.qualification.unique_events_observed != self.unique_events_observed:
            raise ValueError("qualification event count mismatch")
        if self.qualification.events_available_within_latency != self.successful_event_count:
            raise ValueError("qualification success count mismatch")
        expected_manual = self.qualification.status == "QUALIFIED_FOR_MANUAL_REVIEW"
        if self.manual_review_required != expected_manual:
            raise ValueError("manual_review_required must mirror manual-review qualification")
        if self.live_confirmation_enabled or self.provider_network_called or self.store_written or self.runtime_mutated:
            raise ValueError("read-only report cannot enable, call, write, or mutate runtime")
        if self.direction_generated or self.options_trade_generated or self.futures_trade_generated:
            raise ValueError("read-only report cannot generate direction or trades")
        if self.successful_event_count == 0:
            if any(value is not None for value in (
                self.successful_event_latency_min_seconds,
                self.successful_event_latency_median_seconds,
                self.successful_event_latency_max_seconds,
            )):
                raise ValueError("latency summary must be empty without successful events")
        else:
            values = (
                self.successful_event_latency_min_seconds,
                self.successful_event_latency_median_seconds,
                self.successful_event_latency_max_seconds,
            )
            if any(value is None for value in values):
                raise ValueError("successful events require complete latency summary")
            if not (values[0] <= values[1] <= values[2]):
                raise ValueError("latency summary must be ordered min <= median <= max")
        for event in self.events:
            event.validated()
        return self


def _group_attempts(attempts: Iterable[MacroLiveAvailabilityAttempt]) -> dict[str, list[MacroLiveAvailabilityAttempt]]:
    grouped: dict[str, list[MacroLiveAvailabilityAttempt]] = {}
    identities: dict[str, tuple[str, datetime]] = {}
    for attempt in attempts:
        attempt.validated()
        if attempt.provider != "MASSIVE_CME_FUTURES":
            raise ValueError("report accepts only Massive/CME availability attempts")
        identity = (attempt.event_type, _utc(attempt.release_at))
        previous = identities.setdefault(attempt.event_key, identity)
        if previous != identity:
            raise ValueError("same event_key cannot represent multiple event identities")
        grouped.setdefault(attempt.event_key, []).append(attempt)
    for rows in grouped.values():
        rows.sort(key=lambda row: (_utc(row.attempted_at), _utc(row.completed_at), row.fingerprint()))
    return grouped


def build_macro_live_availability_report(
    attempts: Iterable[MacroLiveAvailabilityAttempt],
    *,
    policy: MacroLiveAvailabilityPolicy | None = None,
    generated_at: datetime | None = None,
) -> MacroLiveAvailabilityReport:
    """Build a pure descriptive report from previously persisted attempts."""
    audit_policy = (policy or MacroLiveAvailabilityPolicy()).validated()
    frozen_attempts = tuple(attempt.validated() for attempt in attempts)
    grouped = _group_attempts(frozen_attempts)
    qualification = qualify_massive_live_availability(frozen_attempts, policy=audit_policy)

    event_reports: list[MacroLiveAvailabilityEventReport] = []
    successful_latencies: list[float] = []
    type_counts = {"CPI": 0, "EMPLOYMENT_SITUATION": 0}
    type_success_counts = {"CPI": 0, "EMPLOYMENT_SITUATION": 0}
    too_late_only = 0
    unavailable_only = 0

    for event_key, rows in sorted(grouped.items(), key=lambda item: (_utc(item[1][0].release_at), item[0])):
        first = rows[0]
        type_counts[first.event_type] += 1
        successes = [row for row in rows if row.status == "AVAILABLE_WITHIN_LATENCY"]
        too_late = [row for row in rows if row.status == "AVAILABLE_TOO_LATE"]
        failures = [row for row in rows if row.status == "UNAVAILABLE_OR_PROVIDER_ERROR"]
        earliest_success = min(successes, key=lambda row: (_utc(row.completed_at), row.fingerprint())) if successes else None
        if earliest_success is not None:
            type_success_counts[first.event_type] += 1
            success_latency = float(earliest_success.availability_latency_seconds)
            successful_latencies.append(success_latency)
        elif too_late:
            too_late_only += 1
        else:
            unavailable_only += 1

        report = MacroLiveAvailabilityEventReport(
            event_key=event_key,
            event_type=first.event_type,
            release_at=_utc(first.release_at),
            attempt_count=len(rows),
            earliest_attempt_at=_utc(rows[0].attempted_at),
            latest_attempt_at=_utc(rows[-1].attempted_at),
            terminal_status=rows[-1].status,
            earliest_in_latency_success_at=None if earliest_success is None else _utc(earliest_success.completed_at),
            earliest_in_latency_success_seconds=None if earliest_success is None else float(earliest_success.availability_latency_seconds),
            minimum_observed_latency_seconds=min(float(row.availability_latency_seconds) for row in rows),
            maximum_observed_latency_seconds=max(float(row.availability_latency_seconds) for row in rows),
            has_in_latency_success=bool(successes),
            has_too_late_observation=bool(too_late),
            has_provider_failure=bool(failures),
        ).validated()
        event_reports.append(report)

    success_count = len(successful_latencies)
    if successful_latencies:
        minimum = min(successful_latencies)
        middle = float(median(successful_latencies))
        maximum = max(successful_latencies)
    else:
        minimum = middle = maximum = None

    return MacroLiveAvailabilityReport(
        generated_at=_utc(generated_at or datetime.now(timezone.utc)),
        qualification=qualification,
        unique_events_observed=len(event_reports),
        successful_event_count=success_count,
        too_late_only_event_count=too_late_only,
        unavailable_only_event_count=unavailable_only,
        event_type_counts=type_counts,
        event_type_success_counts=type_success_counts,
        successful_event_latency_min_seconds=minimum,
        successful_event_latency_median_seconds=middle,
        successful_event_latency_max_seconds=maximum,
        events=tuple(event_reports),
        manual_review_required=qualification.status == "QUALIFIED_FOR_MANUAL_REVIEW",
    ).validated()


async def build_macro_live_availability_report_from_store(
    store: Any,
    *,
    policy: MacroLiveAvailabilityPolicy | None = None,
    generated_at: datetime | None = None,
) -> MacroLiveAvailabilityReport:
    """Read persisted attempts only; never initialize, write, or call providers."""
    if not hasattr(store, "list_attempts"):
        raise ValueError("store must expose list_attempts")
    attempts = await _await_result(store.list_attempts())
    if not isinstance(attempts, list):
        raise ValueError("store.list_attempts must return a list")
    return build_macro_live_availability_report(
        attempts,
        policy=policy,
        generated_at=generated_at,
    )


def macro_live_availability_report_payload(report: MacroLiveAvailabilityReport) -> dict:
    """Return an explicit API-safe payload with no provider/database credentials."""
    report = report.validated()
    qualification = report.qualification
    return {
        "status": "MACRO_LIVE_AVAILABILITY_REPORT_READY",
        "generated_at": _utc(report.generated_at).isoformat(),
        "qualification": {
            "status": qualification.status,
            "unique_events_observed": qualification.unique_events_observed,
            "events_available_within_latency": qualification.events_available_within_latency,
            "failed_event_keys": list(qualification.failed_event_keys),
            "max_latency_seconds": qualification.max_latency_seconds,
            "min_unique_events": qualification.min_unique_events,
            "auto_enable_live_confirmation": False,
        },
        "coverage": {
            "unique_events_observed": report.unique_events_observed,
            "successful_event_count": report.successful_event_count,
            "too_late_only_event_count": report.too_late_only_event_count,
            "unavailable_only_event_count": report.unavailable_only_event_count,
            "event_type_counts": dict(report.event_type_counts),
            "event_type_success_counts": dict(report.event_type_success_counts),
        },
        "successful_event_latency_seconds": {
            "min": report.successful_event_latency_min_seconds,
            "median": report.successful_event_latency_median_seconds,
            "max": report.successful_event_latency_max_seconds,
        },
        "events": [
            {
                "event_key": event.event_key,
                "event_type": event.event_type,
                "release_at": _utc(event.release_at).isoformat(),
                "attempt_count": event.attempt_count,
                "earliest_attempt_at": _utc(event.earliest_attempt_at).isoformat(),
                "latest_attempt_at": _utc(event.latest_attempt_at).isoformat(),
                "terminal_status": event.terminal_status,
                "earliest_in_latency_success_at": (
                    None
                    if event.earliest_in_latency_success_at is None
                    else _utc(event.earliest_in_latency_success_at).isoformat()
                ),
                "earliest_in_latency_success_seconds": event.earliest_in_latency_success_seconds,
                "minimum_observed_latency_seconds": event.minimum_observed_latency_seconds,
                "maximum_observed_latency_seconds": event.maximum_observed_latency_seconds,
                "has_in_latency_success": event.has_in_latency_success,
                "has_too_late_observation": event.has_too_late_observation,
                "has_provider_failure": event.has_provider_failure,
            }
            for event in report.events
        ],
        "manual_review_required": report.manual_review_required,
        "live_confirmation_enabled": False,
        "provider_network_called": False,
        "store_written": False,
        "runtime_mutated": False,
        "direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }


def architecture_contract() -> dict:
    return {
        "version": "MASSIVE_MACRO_LIVE_AVAILABILITY_REPORT_V1",
        "read_only": True,
        "provider_network_call_allowed": False,
        "store_write_allowed": False,
        "store_initialization_allowed": False,
        "runtime_mutation_allowed": False,
        "duplicate_attempts_increase_unique_event_sample": False,
        "event_identity_collision_allowed": False,
        "market_direction_or_returns_required": False,
        "pnl_or_option_outcomes_read": False,
        "qualification_reused_from_core_audit": True,
        "qualified_state_requires_manual_review": True,
        "live_confirmation_auto_enabled": False,
        "api_payload_contains_credentials": False,
        "direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
