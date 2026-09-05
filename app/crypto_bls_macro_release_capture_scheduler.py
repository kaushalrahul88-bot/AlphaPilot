"""Prospective BLS exact-release capture into the immutable BTC PIT archive.

Each target declares the exact event key AlphaPilot expects. This is essential
for BLS rolling/latest-release URLs: before a new publication, the same URL may
still expose the previous release. A parsed event whose key does not match the
configured target is rejected and never archived.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

from app.bls_exact_macro_release_provider import BlsEventType, BlsExactMacroReleaseProvider
from app.crypto_macro_event_pit import RELEASE_DATASET, official_macro_release_archive_record


async def _insert(store: Any, record) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("BLS macro PIT store insert_first_seen must return a dict")
    return result


@dataclass(frozen=True)
class BlsReleaseCaptureTarget:
    url: str
    event_type: BlsEventType
    expected_event_key: str

    def validated(self) -> "BlsReleaseCaptureTarget":
        if not str(self.url or "").startswith("https://www.bls.gov/news.release/"):
            raise ValueError("BLS release target must use official https://www.bls.gov/news.release/ URL")
        if self.event_type not in {"CPI", "EMPLOYMENT_SITUATION"}:
            raise ValueError("BLS release target event_type must be CPI or EMPLOYMENT_SITUATION")
        expected_prefix = "BLS:CPI:" if self.event_type == "CPI" else "BLS:EMPLOYMENT_SITUATION:"
        if not str(self.expected_event_key or "").startswith(expected_prefix):
            raise ValueError("BLS expected_event_key does not match target event_type")
        return self


@dataclass(frozen=True)
class BlsReleaseCapturePolicy:
    enabled: bool = False
    poll_seconds: int = 30

    def validated(self) -> "BlsReleaseCapturePolicy":
        if int(self.poll_seconds) < 10:
            raise ValueError("BLS exact-release poll_seconds must be >= 10")
        return self


class BlsExactReleasePitCaptureScheduler:
    def __init__(
        self,
        *,
        provider: BlsExactMacroReleaseProvider,
        store: Any,
        targets: tuple[BlsReleaseCaptureTarget, ...] | list[BlsReleaseCaptureTarget],
        policy: BlsReleaseCapturePolicy | None = None,
    ):
        self.provider = provider
        self.store = store
        self.policy = (policy or BlsReleaseCapturePolicy()).validated()
        validated = [target.validated() for target in targets]
        identities = [(target.event_type, target.expected_event_key) for target in validated]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate BLS exact-release capture target")
        if self.policy.enabled and not validated:
            raise ValueError("enabled BLS exact-release scheduler requires at least one target")
        self.targets = tuple(validated)
        self.cycles = 0
        self.inserted_records = 0
        self.idempotent_duplicates = 0
        self.failures = 0

    async def run_cycle(self) -> dict:
        if not self.policy.enabled:
            return {
                "status": "BLS_EXACT_RELEASE_CAPTURE_DISABLED",
                "provider_calls": 0,
                "captured": [],
                "errors": [],
                "trade_generated": False,
            }

        self.cycles += 1
        captured: list[dict] = []
        errors: list[dict] = []
        provider_calls = 0
        for target in self.targets:
            try:
                provider_calls += 1
                release = await asyncio.to_thread(
                    self.provider.fetch_release,
                    url=target.url,
                    event_type=target.event_type,
                )
                if release.event_key != target.expected_event_key:
                    raise ValueError(
                        f"BLS target expected {target.expected_event_key!r} but official page currently exposes {release.event_key!r}"
                    )
                record = official_macro_release_archive_record(release)
                stored = await _insert(self.store, record)
                storage_status = stored.get("status")
                if storage_status == "INSERTED_FIRST_SEEN":
                    self.inserted_records += 1
                elif storage_status == "IDEMPOTENT_DUPLICATE":
                    self.idempotent_duplicates += 1
                else:
                    raise ValueError(f"unexpected BLS release PIT storage status: {storage_status!r}")
                captured.append({
                    "dataset": RELEASE_DATASET,
                    "event_key": release.event_key,
                    "event_type": release.event_type,
                    "release_at": release.normalized_release_at.isoformat(),
                    "first_seen_at": release.normalized_first_seen_at.isoformat(),
                    "storage_status": storage_status,
                    "consensus_present": False,
                    "surprise_direction_assigned": False,
                    "trade_generated": False,
                })
            except Exception as exc:
                self.failures += 1
                errors.append({
                    "expected_event_key": target.expected_event_key,
                    "event_type": target.event_type,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                    "wrong_or_missing_release_treated_as_neutral": False,
                })

        status = "BLS_EXACT_RELEASE_CAPTURE_CYCLE_COMPLETE" if not errors else "BLS_EXACT_RELEASE_CAPTURE_CYCLE_PARTIAL_FAILURE"
        if errors and not captured:
            status = "BLS_EXACT_RELEASE_CAPTURE_CYCLE_FAILURE"
        return {
            "status": status,
            "provider_calls": provider_calls,
            "captured": captured,
            "errors": errors,
            "state": {
                "cycles": self.cycles,
                "inserted_records": self.inserted_records,
                "idempotent_duplicates": self.idempotent_duplicates,
                "failures": self.failures,
            },
            "trade_generated": False,
        }

    async def run_until_stopped(self, stop_event: asyncio.Event) -> dict:
        if not self.policy.enabled:
            return {"status": "BLS_EXACT_RELEASE_CAPTURE_DISABLED", "cycles": 0, "trade_generated": False}
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=int(self.policy.poll_seconds))
            except TimeoutError:
                pass
        return {"status": "BLS_EXACT_RELEASE_CAPTURE_STOPPED", "cycles": self.cycles, "trade_generated": False}


def architecture_contract() -> dict:
    return {
        "version": "BLS_EXACT_RELEASE_CAPTURE_SCHEDULER_V1",
        "enabled_by_default": False,
        "minimum_poll_seconds": 10,
        "expected_event_key_required": True,
        "rolling_url_previous_release_may_be_archived_as_new_event": False,
        "official_first_seen_preserved": True,
        "wrong_or_missing_release_treated_as_neutral": False,
        "consensus_supplied_by_scheduler": False,
        "surprise_direction_assigned": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "automatic_startup_registration": False,
        "research_only": True,
    }
