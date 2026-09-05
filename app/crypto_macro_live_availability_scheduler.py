"""Disabled-by-default scheduler for prospective Massive/CME availability audits.

The scheduler never treats historical reconstruction or a configured subscription
label as live proof. It starts only after an event reaction window completes,
retries inside the allowed latency budget, persists every attempt, and can make
one terminal observation after the deadline when no terminal check exists.
Qualification remains manual-review-only and never changes trading state.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from app.crypto_macro_live_availability_audit import (
    MacroLiveAvailabilityAttempt,
    MacroLiveAvailabilityPolicy,
    audit_massive_live_availability_once,
    qualify_massive_live_availability,
)
from app.massive_macro_futures_reaction_provider import (
    MacroEventType,
    MassiveMacroFuturesReactionProvider,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("macro availability scheduler time must be timezone-aware")
    return value.astimezone(timezone.utc)


async def _await_result(value):
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass(frozen=True)
class MacroLiveAvailabilityTarget:
    event_key: str
    event_type: MacroEventType
    release_at: datetime

    @property
    def release_at_utc(self) -> datetime:
        return _utc(self.release_at)

    def validated(self) -> "MacroLiveAvailabilityTarget":
        if not str(self.event_key or "").strip():
            raise ValueError("event_key is required")
        if self.event_type not in {"CPI", "EMPLOYMENT_SITUATION"}:
            raise ValueError("unsupported macro availability event_type")
        self.release_at_utc
        return self


@dataclass(frozen=True)
class MacroLiveAvailabilityCapturePolicy:
    enabled: bool = False
    poll_seconds: int = 15
    max_latency_seconds: float = 120.0
    min_unique_events: int = 3

    def validated(self) -> "MacroLiveAvailabilityCapturePolicy":
        MacroLiveAvailabilityPolicy(
            max_latency_seconds=self.max_latency_seconds,
            min_unique_events=self.min_unique_events,
        ).validated()
        if not 5 <= int(self.poll_seconds) <= 60:
            raise ValueError("macro availability poll_seconds must be between 5 and 60")
        if float(self.poll_seconds) > float(self.max_latency_seconds):
            raise ValueError("poll_seconds cannot exceed max_latency_seconds")
        return self

    @property
    def audit_policy(self) -> MacroLiveAvailabilityPolicy:
        self.validated()
        return MacroLiveAvailabilityPolicy(
            max_latency_seconds=self.max_latency_seconds,
            min_unique_events=self.min_unique_events,
        )


class MacroLiveAvailabilityAuditScheduler:
    def __init__(
        self,
        *,
        provider: MassiveMacroFuturesReactionProvider,
        store: Any,
        targets: Iterable[MacroLiveAvailabilityTarget],
        policy: MacroLiveAvailabilityCapturePolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.provider = provider
        self.store = store
        self.targets = tuple(target.validated() for target in targets)
        identities = [target.event_key for target in self.targets]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate macro live-availability target event_key")
        self.policy = (policy or MacroLiveAvailabilityCapturePolicy()).validated()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.cycles = 0
        self.attempts_inserted = 0
        self.idempotent_attempts = 0
        self.store_failures = 0

    async def _stored_attempts(self) -> list[MacroLiveAvailabilityAttempt]:
        rows = await _await_result(self.store.list_attempts())
        if not isinstance(rows, list) or not all(isinstance(row, MacroLiveAvailabilityAttempt) for row in rows):
            raise ValueError("macro live-availability store list_attempts must return attempt objects")
        return [row.validated() for row in rows]

    @staticmethod
    def _event_attempts(attempts: list[MacroLiveAvailabilityAttempt], event_key: str) -> list[MacroLiveAvailabilityAttempt]:
        return sorted(
            (row for row in attempts if row.event_key == event_key),
            key=lambda row: (_utc(row.attempted_at), _utc(row.completed_at), row.fingerprint()),
        )

    async def _insert(self, attempt: MacroLiveAvailabilityAttempt) -> dict:
        stored = await _await_result(self.store.insert_attempt(attempt))
        if not isinstance(stored, dict):
            raise ValueError("macro live-availability store insert_attempt must return dict")
        status = stored.get("status")
        if status == "INSERTED_AVAILABILITY_ATTEMPT":
            self.attempts_inserted += 1
        elif status == "IDEMPOTENT_AVAILABILITY_ATTEMPT":
            self.idempotent_attempts += 1
        else:
            raise ValueError(f"unexpected macro availability storage status: {status!r}")
        return stored

    async def run_cycle(self, *, now: datetime | None = None) -> dict:
        stamp = _utc(now or self._clock())
        if not self.policy.enabled:
            return {
                "status": "MACRO_LIVE_AVAILABILITY_AUDIT_DISABLED",
                "provider_called": False,
                "store_written": False,
                "attempts": [],
                "pending_targets": [],
                "closed_targets": [],
                "errors": [],
                "qualification": None,
                "live_confirmation_enabled": False,
                "trade_generated": False,
            }

        self.cycles += 1
        stored_attempts = await self._stored_attempts()
        attempted_rows: list[dict] = []
        pending_targets: list[str] = []
        closed_targets: list[str] = []
        errors: list[dict] = []
        provider_called = False
        store_written = False

        reaction_minutes = int(self.provider.policy.reaction_window_minutes)
        latency_budget = timedelta(seconds=float(self.policy.max_latency_seconds))
        poll_delta = timedelta(seconds=int(self.policy.poll_seconds))

        for target in self.targets:
            window_end = target.release_at_utc + timedelta(minutes=reaction_minutes)
            deadline = window_end + latency_budget
            prior = self._event_attempts(stored_attempts, target.event_key)
            if any(row.status == "AVAILABLE_WITHIN_LATENCY" for row in prior):
                closed_targets.append(target.event_key)
                continue
            if stamp < window_end:
                pending_targets.append(target.event_key)
                continue

            latest = prior[-1] if prior else None
            if latest is not None and _utc(latest.attempted_at) >= deadline:
                closed_targets.append(target.event_key)
                continue
            if latest is not None and stamp <= deadline and stamp - _utc(latest.attempted_at) < poll_delta:
                pending_targets.append(target.event_key)
                continue

            # Inside the latency budget we retry on the configured cadence. Once
            # the budget has elapsed, exactly one post-deadline terminal attempt
            # is allowed if no such attempt is already persisted.
            provider_called = True
            try:
                attempt = await asyncio.to_thread(
                    audit_massive_live_availability_once,
                    self.provider,
                    event_key=target.event_key,
                    event_type=target.event_type,
                    release_at=target.release_at_utc,
                    policy=self.policy.audit_policy,
                    clock=self._clock,
                )
                stored = await self._insert(attempt)
                store_written = store_written or stored.get("status") == "INSERTED_AVAILABILITY_ATTEMPT"
                stored_attempts.append(attempt)
                attempted_rows.append({
                    "event_key": target.event_key,
                    "status": attempt.status,
                    "attempted_at": _utc(attempt.attempted_at).isoformat(),
                    "completed_at": _utc(attempt.completed_at).isoformat(),
                    "availability_latency_seconds": attempt.availability_latency_seconds,
                    "storage_status": stored.get("status"),
                    "live_confirmation_enabled": False,
                    "direction_generated": False,
                    "trade_generated": False,
                })
                if attempt.status == "AVAILABLE_WITHIN_LATENCY" or _utc(attempt.attempted_at) >= deadline:
                    closed_targets.append(target.event_key)
            except Exception as exc:
                self.store_failures += 1
                errors.append({
                    "event_key": target.event_key,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                    "provider_failure_treated_as_neutral": False,
                    "live_confirmation_enabled": False,
                })

        qualification = qualify_massive_live_availability(
            stored_attempts,
            policy=self.policy.audit_policy,
        )
        return {
            "status": (
                "MACRO_LIVE_AVAILABILITY_AUDIT_CYCLE_COMPLETE"
                if not errors else "MACRO_LIVE_AVAILABILITY_AUDIT_CYCLE_PARTIAL_FAILURE"
            ),
            "provider_called": provider_called,
            "store_written": store_written,
            "attempts": attempted_rows,
            "pending_targets": sorted(set(pending_targets)),
            "closed_targets": sorted(set(closed_targets)),
            "errors": errors,
            "qualification": {
                "status": qualification.status,
                "unique_events_observed": qualification.unique_events_observed,
                "events_available_within_latency": qualification.events_available_within_latency,
                "failed_event_keys": list(qualification.failed_event_keys),
                "auto_enable_live_confirmation": False,
            },
            "state": {
                "cycles": self.cycles,
                "attempts_inserted": self.attempts_inserted,
                "idempotent_attempts": self.idempotent_attempts,
                "store_failures": self.store_failures,
            },
            "live_confirmation_enabled": False,
            "trade_generated": False,
        }

    async def run_until_stopped(self, stop_event: asyncio.Event) -> dict:
        if not self.policy.enabled:
            return {
                "status": "MACRO_LIVE_AVAILABILITY_AUDIT_DISABLED",
                "cycles": 0,
                "live_confirmation_enabled": False,
                "trade_generated": False,
            }
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=int(self.policy.poll_seconds))
            except TimeoutError:
                pass
        return {
            "status": "MACRO_LIVE_AVAILABILITY_AUDIT_STOPPED",
            "cycles": self.cycles,
            "live_confirmation_enabled": False,
            "trade_generated": False,
        }


def architecture_contract() -> dict:
    return {
        "version": "MASSIVE_MACRO_LIVE_AVAILABILITY_SCHEDULER_V1",
        "enabled_by_default": False,
        "scheduler_starts_at_import": False,
        "reaction_window_completion_required_before_provider_call": True,
        "retries_allowed_inside_latency_budget": True,
        "terminal_post_deadline_attempt_allowed": True,
        "stored_attempts_reloaded_each_cycle": True,
        "restart_resets_qualification_history": False,
        "provider_failure_treated_as_neutral": False,
        "qualification_is_manual_review_only": True,
        "live_confirmation_auto_enabled": False,
        "direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
