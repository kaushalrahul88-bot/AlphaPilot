"""Prospective availability audit for Massive/CME macro-reaction data.

Historical reconstruction is not proof that the same data was available quickly
enough after a live macro release. Massive futures plans can be delayed or
real-time, so AlphaPilot proves availability from actual prospective retrieval
attempts instead of trusting a configured plan label.

The audit is operational evidence only. Even a fully qualified sample is marked
``QUALIFIED_FOR_MANUAL_REVIEW`` and cannot auto-enable macro direction, Options,
Futures, execution, or capital.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Callable, Iterable, Literal

from app.massive_macro_futures_reaction_provider import (
    EURO_FX_PRODUCT_CODE,
    NQ_PRODUCT_CODE,
    MacroEventType,
    MassiveMacroFuturesReaction,
    MassiveMacroFuturesReactionProvider,
)

AttemptStatus = Literal[
    "AVAILABLE_WITHIN_LATENCY",
    "AVAILABLE_TOO_LATE",
    "UNAVAILABLE_OR_PROVIDER_ERROR",
]
QualificationStatus = Literal[
    "INSUFFICIENT_PROSPECTIVE_EVENTS",
    "NOT_QUALIFIED",
    "QUALIFIED_FOR_MANUAL_REVIEW",
]


def _utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MacroLiveAvailabilityPolicy:
    max_latency_seconds: float = 120.0
    min_unique_events: int = 3

    def validated(self) -> "MacroLiveAvailabilityPolicy":
        latency = float(self.max_latency_seconds)
        if not isfinite(latency) or not 5.0 <= latency < 600.0:
            raise ValueError("max_latency_seconds must be finite, >= 5 and < 600")
        if not 2 <= int(self.min_unique_events) <= 20:
            raise ValueError("min_unique_events must be between 2 and 20")
        return self


@dataclass(frozen=True)
class MacroLiveAvailabilityAttempt:
    event_key: str
    event_type: MacroEventType
    release_at: datetime
    reaction_window_end: datetime
    attempted_at: datetime
    completed_at: datetime
    status: AttemptStatus
    availability_latency_seconds: float
    provider: str = "MASSIVE_CME_FUTURES"
    nasdaq_contract_ticker: str | None = None
    euro_fx_contract_ticker: str | None = None
    failure_kind: str | None = None
    historical_reconstruction_used_as_live_proof: bool = False
    direction_generated: bool = False
    options_trade_generated: bool = False
    futures_trade_generated: bool = False
    live_confirmation_enabled: bool = False

    def validated(self) -> "MacroLiveAvailabilityAttempt":
        release = _utc(self.release_at, name="release_at")
        window_end = _utc(self.reaction_window_end, name="reaction_window_end")
        attempted = _utc(self.attempted_at, name="attempted_at")
        completed = _utc(self.completed_at, name="completed_at")
        if self.event_type not in {"CPI", "EMPLOYMENT_SITUATION"}:
            raise ValueError("unsupported macro event_type")
        if not str(self.event_key or "").strip():
            raise ValueError("event_key is required")
        if window_end <= release:
            raise ValueError("reaction_window_end must be after release_at")
        if attempted < window_end:
            raise ValueError("availability attempt cannot begin before the reaction window completes")
        if completed < attempted:
            raise ValueError("completed_at cannot precede attempted_at")
        latency = float(self.availability_latency_seconds)
        if not isfinite(latency) or latency < 0:
            raise ValueError("availability_latency_seconds must be finite and >= 0")
        actual_latency = (completed - window_end).total_seconds()
        if abs(actual_latency - latency) > 1e-6:
            raise ValueError("availability latency must be measured from completed reaction window")
        if self.status not in {
            "AVAILABLE_WITHIN_LATENCY", "AVAILABLE_TOO_LATE", "UNAVAILABLE_OR_PROVIDER_ERROR"
        }:
            raise ValueError("unsupported availability attempt status")
        available = self.status in {"AVAILABLE_WITHIN_LATENCY", "AVAILABLE_TOO_LATE"}
        if available:
            if not self.nasdaq_contract_ticker or not self.euro_fx_contract_ticker:
                raise ValueError("available attempt requires selected NQ and 6E contract tickers")
            if self.failure_kind is not None:
                raise ValueError("available attempt cannot contain failure_kind")
        else:
            if self.nasdaq_contract_ticker is not None or self.euro_fx_contract_ticker is not None:
                raise ValueError("failed attempt cannot claim selected contracts")
            if not str(self.failure_kind or "").strip():
                raise ValueError("failed attempt requires failure_kind")
        if self.historical_reconstruction_used_as_live_proof:
            raise ValueError("historical reconstruction cannot be labelled prospective live proof")
        if self.direction_generated or self.options_trade_generated or self.futures_trade_generated:
            raise ValueError("availability audit cannot generate direction or trades")
        if self.live_confirmation_enabled:
            raise ValueError("availability audit cannot auto-enable live confirmation")
        return self

    def fingerprint(self) -> str:
        self.validated()
        payload = {
            "event_key": self.event_key,
            "event_type": self.event_type,
            "release_at": _utc(self.release_at, name="release_at").isoformat(),
            "reaction_window_end": _utc(self.reaction_window_end, name="reaction_window_end").isoformat(),
            "attempted_at": _utc(self.attempted_at, name="attempted_at").isoformat(),
            "completed_at": _utc(self.completed_at, name="completed_at").isoformat(),
            "status": self.status,
            "availability_latency_seconds": round(float(self.availability_latency_seconds), 6),
            "provider": self.provider,
            "nasdaq_contract_ticker": self.nasdaq_contract_ticker,
            "euro_fx_contract_ticker": self.euro_fx_contract_ticker,
            "failure_kind": self.failure_kind,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MacroLiveAvailabilityQualification:
    status: QualificationStatus
    unique_events_observed: int
    events_available_within_latency: int
    failed_event_keys: tuple[str, ...]
    max_latency_seconds: float
    min_unique_events: int
    auto_enable_live_confirmation: bool = False
    direction_generated: bool = False
    options_trade_generated: bool = False
    futures_trade_generated: bool = False

    def validated(self) -> "MacroLiveAvailabilityQualification":
        if self.status not in {
            "INSUFFICIENT_PROSPECTIVE_EVENTS", "NOT_QUALIFIED", "QUALIFIED_FOR_MANUAL_REVIEW"
        }:
            raise ValueError("unsupported availability qualification status")
        if int(self.unique_events_observed) < 0 or int(self.events_available_within_latency) < 0:
            raise ValueError("event counts must be nonnegative")
        if self.events_available_within_latency > self.unique_events_observed:
            raise ValueError("available event count cannot exceed observed event count")
        if self.auto_enable_live_confirmation:
            raise ValueError("qualification cannot auto-enable live confirmation")
        if self.direction_generated or self.options_trade_generated or self.futures_trade_generated:
            raise ValueError("qualification cannot generate direction or trades")
        return self


def _validate_reaction_identity(
    reaction: MassiveMacroFuturesReaction,
    *,
    event_key: str,
    event_type: MacroEventType,
    release_at: datetime,
    expected_window_end: datetime,
) -> MassiveMacroFuturesReaction:
    reaction.validated()
    if reaction.event_key != event_key or reaction.event_type != event_type:
        raise ValueError("Massive reaction event identity mismatch")
    if _utc(reaction.release_at, name="reaction.release_at") != release_at:
        raise ValueError("Massive reaction release timestamp mismatch")
    if _utc(reaction.observed_at, name="reaction.observed_at") != expected_window_end:
        raise ValueError("Massive reaction window does not match provider policy")
    if reaction.nasdaq_contract.product_code != NQ_PRODUCT_CODE:
        raise ValueError("Massive reaction does not contain NQ contract")
    if reaction.euro_fx_contract.product_code != EURO_FX_PRODUCT_CODE:
        raise ValueError("Massive reaction does not contain 6E contract")
    return reaction


def audit_massive_live_availability_once(
    provider: MassiveMacroFuturesReactionProvider,
    *,
    event_key: str,
    event_type: MacroEventType,
    release_at: datetime,
    policy: MacroLiveAvailabilityPolicy | None = None,
    clock: Callable[[], datetime] | None = None,
) -> MacroLiveAvailabilityAttempt:
    """Attempt one prospective retrieval and freeze the observed availability result."""
    audit_policy = (policy or MacroLiveAvailabilityPolicy()).validated()
    release = _utc(release_at, name="release_at")
    provider.policy.validated()
    window_end = release + timedelta(minutes=int(provider.policy.reaction_window_minutes))
    now = clock or (lambda: datetime.now(timezone.utc))
    attempted = _utc(now(), name="attempted_at")
    if attempted < window_end:
        # Fail before any provider request; an unfinished event window is not an
        # availability observation.
        raise ValueError("prospective availability audit cannot run before reaction window completion")

    try:
        reaction = provider.fetch_reaction(
            event_key=event_key,
            event_type=event_type,
            release_at=release,
        )
        completed = _utc(now(), name="completed_at")
        reaction = _validate_reaction_identity(
            reaction,
            event_key=event_key,
            event_type=event_type,
            release_at=release,
            expected_window_end=window_end,
        )
        latency = (completed - window_end).total_seconds()
        status: AttemptStatus = (
            "AVAILABLE_WITHIN_LATENCY"
            if latency <= float(audit_policy.max_latency_seconds)
            else "AVAILABLE_TOO_LATE"
        )
        return MacroLiveAvailabilityAttempt(
            event_key=event_key,
            event_type=event_type,
            release_at=release,
            reaction_window_end=window_end,
            attempted_at=attempted,
            completed_at=completed,
            status=status,
            availability_latency_seconds=latency,
            nasdaq_contract_ticker=reaction.nasdaq_contract.ticker,
            euro_fx_contract_ticker=reaction.euro_fx_contract.ticker,
        ).validated()
    except Exception as exc:  # operational audit must preserve explicit failure
        completed = _utc(now(), name="completed_at")
        latency = (completed - window_end).total_seconds()
        return MacroLiveAvailabilityAttempt(
            event_key=event_key,
            event_type=event_type,
            release_at=release,
            reaction_window_end=window_end,
            attempted_at=attempted,
            completed_at=completed,
            status="UNAVAILABLE_OR_PROVIDER_ERROR",
            availability_latency_seconds=latency,
            failure_kind=type(exc).__name__,
        ).validated()


def qualify_massive_live_availability(
    attempts: Iterable[MacroLiveAvailabilityAttempt],
    *,
    policy: MacroLiveAvailabilityPolicy | None = None,
) -> MacroLiveAvailabilityQualification:
    """Qualify only repeated prospective success; never auto-enable live use."""
    audit_policy = (policy or MacroLiveAvailabilityPolicy()).validated()
    grouped: dict[str, list[MacroLiveAvailabilityAttempt]] = {}
    event_types: dict[str, str] = {}
    release_times: dict[str, datetime] = {}
    for attempt in attempts:
        attempt.validated()
        if attempt.provider != "MASSIVE_CME_FUTURES":
            raise ValueError("qualification accepts only Massive/CME availability attempts")
        release = _utc(attempt.release_at, name="release_at")
        previous_type = event_types.setdefault(attempt.event_key, attempt.event_type)
        previous_release = release_times.setdefault(attempt.event_key, release)
        if previous_type != attempt.event_type or previous_release != release:
            raise ValueError("same event_key cannot represent multiple event identities")
        grouped.setdefault(attempt.event_key, []).append(attempt)

    unique_events = len(grouped)
    successful = 0
    failed: list[str] = []
    for event_key, rows in grouped.items():
        if any(row.status == "AVAILABLE_WITHIN_LATENCY" for row in rows):
            successful += 1
        else:
            failed.append(event_key)

    if unique_events < int(audit_policy.min_unique_events):
        status: QualificationStatus = "INSUFFICIENT_PROSPECTIVE_EVENTS"
    elif failed:
        status = "NOT_QUALIFIED"
    else:
        status = "QUALIFIED_FOR_MANUAL_REVIEW"

    return MacroLiveAvailabilityQualification(
        status=status,
        unique_events_observed=unique_events,
        events_available_within_latency=successful,
        failed_event_keys=tuple(sorted(failed)),
        max_latency_seconds=float(audit_policy.max_latency_seconds),
        min_unique_events=int(audit_policy.min_unique_events),
    ).validated()


def architecture_contract() -> dict:
    return {
        "version": "MASSIVE_MACRO_LIVE_AVAILABILITY_AUDIT_V1",
        "historical_reconstruction_proves_live_availability": False,
        "configured_plan_label_proves_live_availability": False,
        "prospective_retrieval_observation_required": True,
        "reaction_window_must_be_complete_before_attempt": True,
        "latency_measured_from_completed_reaction_window": True,
        "repeated_unique_events_required": True,
        "single_success_auto_qualifies": False,
        "qualification_is_manual_review_only": True,
        "live_confirmation_auto_enabled": False,
        "direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
