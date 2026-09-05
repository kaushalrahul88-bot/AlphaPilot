"""Disabled-by-default PIT scheduler for Trading Economics macro consensus.

Only pre-release survey-consensus snapshots are captured. Once a target reaches
its official expected release timestamp it is closed and the provider is no
longer called for that target. Exact rediscovery is idempotent through the
immutable PIT store; a genuinely changed pre-release consensus state has a new
state hash and is stored separately.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from app.crypto_macro_event_pit import CONSENSUS_DATASET, macro_consensus_archive_record
from app.tradingeconomics_macro_consensus_provider import (
    TradingEconomicsConsensusTarget,
    TradingEconomicsMacroConsensusProvider,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler time must be timezone-aware")
    return value.astimezone(timezone.utc)


async def _insert(store: Any, record) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("Trading Economics consensus PIT store insert_first_seen must return a dict")
    return result


@dataclass(frozen=True)
class TradingEconomicsConsensusCapturePolicy:
    enabled: bool = False
    poll_seconds: int = 300

    def validated(self) -> "TradingEconomicsConsensusCapturePolicy":
        if int(self.poll_seconds) < 60:
            raise ValueError("Trading Economics consensus poll_seconds must be >= 60")
        return self


class TradingEconomicsConsensusPitCaptureScheduler:
    def __init__(
        self,
        *,
        provider: TradingEconomicsMacroConsensusProvider,
        store: Any,
        targets: Iterable[TradingEconomicsConsensusTarget],
        policy: TradingEconomicsConsensusCapturePolicy | None = None,
    ):
        self.provider = provider
        self.store = store
        self.targets = tuple(target.validated() for target in targets)
        identities = [target.event_key for target in self.targets]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate Trading Economics consensus target event_key")
        self.policy = (policy or TradingEconomicsConsensusCapturePolicy()).validated()
        self.cycles = 0
        self.inserted_records = 0
        self.idempotent_duplicates = 0
        self.closed_target_skips = 0
        self.failures = 0

    async def run_cycle(self, *, now: datetime | None = None) -> dict:
        stamp = _utc(now or datetime.now(timezone.utc))
        if not self.policy.enabled:
            return {
                "status": "TRADING_ECONOMICS_CONSENSUS_CAPTURE_DISABLED",
                "provider_called": False,
                "store_written": False,
                "captured": [],
                "closed_targets": [],
                "errors": [],
                "trade_generated": False,
            }

        self.cycles += 1
        captured: list[dict] = []
        closed_targets: list[dict] = []
        errors: list[dict] = []
        provider_called = False

        for target in self.targets:
            if stamp >= target.release_at_utc:
                self.closed_target_skips += 1
                closed_targets.append({
                    "event_key": target.event_key,
                    "release_at": target.release_at_utc.isoformat(),
                    "provider_called": False,
                    "post_release_consensus_captured": False,
                })
                continue
            provider_called = True
            try:
                snapshot = await asyncio.to_thread(self.provider.fetch_consensus, target=target)
                if snapshot.event_key != target.event_key:
                    raise ValueError("Trading Economics provider returned unexpected event_key")
                record = macro_consensus_archive_record(snapshot)
                stored = await _insert(self.store, record)
                storage_status = stored.get("status")
                if storage_status == "INSERTED_FIRST_SEEN":
                    self.inserted_records += 1
                elif storage_status == "IDEMPOTENT_DUPLICATE":
                    self.idempotent_duplicates += 1
                else:
                    raise ValueError(f"unexpected Trading Economics consensus PIT storage status: {storage_status!r}")
                captured.append({
                    "dataset": CONSENSUS_DATASET,
                    "event_key": snapshot.event_key,
                    "event_type": snapshot.event_type,
                    "release_at": snapshot.release_at.astimezone(timezone.utc).isoformat(),
                    "provider_time": None if snapshot.provider_time is None else snapshot.provider_time.astimezone(timezone.utc).isoformat(),
                    "first_seen_at": snapshot.first_seen_at.astimezone(timezone.utc).isoformat(),
                    "state_hash": snapshot.state_hash,
                    "storage_status": storage_status,
                    "survey_consensus_only": True,
                    "numeric_surprise_generated": False,
                    "direction_assigned": False,
                    "trade_generated": False,
                })
            except Exception as exc:
                self.failures += 1
                errors.append({
                    "event_key": target.event_key,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                    "missing_consensus_treated_as_neutral": False,
                    "post_release_consensus_backfilled": False,
                })

        return {
            "status": (
                "TRADING_ECONOMICS_CONSENSUS_CAPTURE_CYCLE_COMPLETE"
                if not errors else "TRADING_ECONOMICS_CONSENSUS_CAPTURE_CYCLE_PARTIAL_FAILURE"
            ),
            "provider_called": provider_called,
            "store_written": any(row["storage_status"] == "INSERTED_FIRST_SEEN" for row in captured),
            "captured": captured,
            "closed_targets": closed_targets,
            "errors": errors,
            "state": {
                "cycles": self.cycles,
                "inserted_records": self.inserted_records,
                "idempotent_duplicates": self.idempotent_duplicates,
                "closed_target_skips": self.closed_target_skips,
                "failures": self.failures,
            },
            "trade_generated": False,
        }

    async def run_until_stopped(self, stop_event: asyncio.Event) -> dict:
        if not self.policy.enabled:
            return {"status": "TRADING_ECONOMICS_CONSENSUS_CAPTURE_DISABLED", "cycles": 0, "trade_generated": False}
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=int(self.policy.poll_seconds))
            except TimeoutError:
                pass
        return {
            "status": "TRADING_ECONOMICS_CONSENSUS_CAPTURE_STOPPED",
            "cycles": self.cycles,
            "trade_generated": False,
        }


def architecture_contract() -> dict:
    return {
        "version": "TRADING_ECONOMICS_CONSENSUS_CAPTURE_SCHEDULER_V1",
        "collection_enabled_by_default": False,
        "scheduler_starts_at_import": False,
        "minimum_poll_seconds": 60,
        "exact_unchanged_repoll_is_idempotent": True,
        "changed_pre_release_consensus_is_new_state": True,
        "target_closes_at_official_release": True,
        "provider_called_after_target_release": False,
        "post_release_consensus_backfill_allowed": False,
        "missing_consensus_treated_as_neutral": False,
        "numeric_surprise_generated": False,
        "direction_assigned": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
