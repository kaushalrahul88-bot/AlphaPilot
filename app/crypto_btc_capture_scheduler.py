"""Disabled-by-default scheduler for irrecoverable BTC point-in-time capture.

The scheduler is deliberately narrow. It may archive verified data collectors,
but it never creates an Options/Futures trade and never starts automatically.
At present only CoinDCX's current BTC Futures funding/mark snapshot has a public
collector implementation. Missing critical collectors are reported as gaps.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.coindcx_btc_public_provider import CoinDcxBtcPublicProvider, CoinDcxFuturesRtCapture
from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture
from app.crypto_btc_source_capabilities import live_capture_plan

COINDCX_FUTURES_RT_JOB = "COINDCX_BTC_FUTURES_FUNDING_MARK"
COINDCX_FUTURES_RT_DATASET = "BTC_FUTURES_FUNDING_MARK_SNAPSHOT"
IMPLEMENTED_DATASETS = frozenset({COINDCX_FUTURES_RT_DATASET})


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else _utc(dt).isoformat()


@dataclass(frozen=True)
class BtcCaptureSchedulerPolicy:
    enabled: bool = False
    poll_seconds: int = 60
    enabled_jobs: tuple[str, ...] = (COINDCX_FUTURES_RT_JOB,)
    continue_after_job_failure: bool = True

    def validated(self) -> "BtcCaptureSchedulerPolicy":
        if int(self.poll_seconds) < 10:
            raise ValueError("BTC capture poll_seconds must be >= 10")
        supported = {COINDCX_FUTURES_RT_JOB}
        unknown = sorted(set(self.enabled_jobs) - supported)
        if unknown:
            raise ValueError(f"unsupported BTC capture jobs: {unknown}")
        if len(set(self.enabled_jobs)) != len(self.enabled_jobs):
            raise ValueError("BTC capture enabled_jobs must be unique")
        return self


@dataclass
class BtcCaptureSchedulerState:
    started_at: datetime | None = None
    last_cycle_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    cycles: int = 0
    inserted_records: int = 0
    idempotent_duplicates: int = 0
    failures: int = 0
    job_last_attempt_at: dict[str, datetime] = field(default_factory=dict)

    def snapshot(self, *, policy: BtcCaptureSchedulerPolicy) -> dict:
        return {
            "version": "BTC_CAPTURE_SCHEDULER_STATE_V1",
            "enabled": policy.enabled,
            "poll_seconds": policy.poll_seconds,
            "enabled_jobs": list(policy.enabled_jobs),
            "started_at": _iso(self.started_at),
            "last_cycle_at": _iso(self.last_cycle_at),
            "last_success_at": _iso(self.last_success_at),
            "last_error_at": _iso(self.last_error_at),
            "cycles": self.cycles,
            "inserted_records": self.inserted_records,
            "idempotent_duplicates": self.idempotent_duplicates,
            "failures": self.failures,
            "job_last_attempt_at": {key: _iso(value) for key, value in sorted(self.job_last_attempt_at.items())},
            "options_trade_generated": False,
            "futures_trade_generated": False,
        }


def coindcx_futures_rt_archive_record(capture: CoinDcxFuturesRtCapture) -> BtcPitArchiveRecord:
    capture.validated()
    first_seen = _utc(capture.first_seen_at)
    provider_event_candidates = [
        stamp for stamp in (capture.provider_snapshot_at, capture.provider_tick_at, capture.mark_price_at)
        if stamp is not None and _utc(stamp) <= first_seen
    ]
    event_at = max((_utc(stamp) for stamp in provider_event_candidates), default=None)
    payload = {
        "pair": capture.raw_pair,
        "market": capture.market,
        "provider_snapshot_at": _iso(capture.provider_snapshot_at),
        "provider_tick_at": _iso(capture.provider_tick_at),
        "mark_price_at": _iso(capture.mark_price_at),
        "funding_rate": capture.funding_rate,
        "estimated_funding_rate": capture.estimated_funding_rate,
        "mark_price": capture.mark_price,
        "last_price": capture.last_price,
        "price_change_pct_24h": capture.price_change_pct_24h,
        "volume_24h": capture.volume_24h,
        "open_interest": None,
        "liquidations": None,
        "open_interest_inferred": False,
        "liquidations_inferred": False,
    }
    source_key = f"{capture.raw_pair}:{int(first_seen.timestamp() * 1000)}"
    return archive_record_from_capture(
        dataset=COINDCX_FUTURES_RT_DATASET,
        provider="COINDCX",
        source_key=source_key,
        first_seen_at=first_seen,
        event_at=event_at,
        source_version="COINDCX_FUTURES_RT_V1",
        payload=payload,
    )


async def _store_insert(store: Any, record: BtcPitArchiveRecord) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("BTC PIT store insert_first_seen must return a dict")
    return result


class BtcPitCaptureScheduler:
    def __init__(
        self,
        *,
        provider: CoinDcxBtcPublicProvider,
        store: Any,
        policy: BtcCaptureSchedulerPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.policy = (policy or BtcCaptureSchedulerPolicy()).validated()
        self.state = BtcCaptureSchedulerState()

    def _job_due(self, job: str, now: datetime) -> bool:
        last = self.state.job_last_attempt_at.get(job)
        return last is None or _utc(now) - _utc(last) >= timedelta(seconds=self.policy.poll_seconds)

    async def _capture_coindcx_futures_rt(self, now: datetime) -> dict:
        capture = await asyncio.to_thread(self.provider.capture_futures_rt, first_seen_at=_utc(now))
        record = coindcx_futures_rt_archive_record(capture)
        stored = await _store_insert(self.store, record)
        return {
            "job": COINDCX_FUTURES_RT_JOB,
            "dataset": COINDCX_FUTURES_RT_DATASET,
            "storage_status": stored.get("status"),
            "natural_key": stored.get("natural_key") or record.natural_key,
            "record_fingerprint": stored.get("record_fingerprint") or record.record_fingerprint,
            "first_seen_at": _iso(record.first_seen_at),
            "options_trade_generated": False,
            "futures_trade_generated": False,
        }

    async def run_cycle(self, *, now: datetime | None = None) -> dict:
        stamp = _utc(now or datetime.now(timezone.utc))
        if not self.policy.enabled:
            return {
                "status": "BTC_CAPTURE_DISABLED",
                "captured": [],
                "state": self.state.snapshot(policy=self.policy),
                "provider_called": False,
                "store_written": False,
            }

        if self.state.started_at is None:
            self.state.started_at = stamp
        self.state.last_cycle_at = stamp
        self.state.cycles += 1
        captured: list[dict] = []
        skipped: list[dict] = []
        errors: list[dict] = []

        for job in self.policy.enabled_jobs:
            if not self._job_due(job, stamp):
                skipped.append({"job": job, "reason": "NOT_DUE"})
                continue
            self.state.job_last_attempt_at[job] = stamp
            try:
                if job == COINDCX_FUTURES_RT_JOB:
                    result = await self._capture_coindcx_futures_rt(stamp)
                else:  # policy validation makes this unreachable; fail closed if contract changes.
                    raise ValueError(f"unsupported BTC capture job: {job}")
                captured.append(result)
                if result["storage_status"] == "INSERTED_FIRST_SEEN":
                    self.state.inserted_records += 1
                elif result["storage_status"] == "IDEMPOTENT_DUPLICATE":
                    self.state.idempotent_duplicates += 1
                self.state.last_success_at = stamp
            except Exception as exc:
                self.state.failures += 1
                self.state.last_error_at = stamp
                errors.append({"job": job, "error_type": exc.__class__.__name__, "message": str(exc)})
                if not self.policy.continue_after_job_failure:
                    break

        return {
            "status": "BTC_CAPTURE_CYCLE_COMPLETE" if not errors else "BTC_CAPTURE_CYCLE_PARTIAL_FAILURE",
            "captured": captured,
            "skipped": skipped,
            "errors": errors,
            "state": self.state.snapshot(policy=self.policy),
            "provider_called": bool(captured or errors),
            "store_written": any(item.get("storage_status") == "INSERTED_FIRST_SEEN" for item in captured),
            "options_trade_generated": False,
            "futures_trade_generated": False,
        }

    async def run_until_stopped(self, stop_event: asyncio.Event) -> dict:
        """Optional service loop. Caller must explicitly enable policy and invoke it."""
        if not self.policy.enabled:
            return {"status": "BTC_CAPTURE_DISABLED", "cycles": 0}
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.policy.poll_seconds)
            except TimeoutError:
                pass
        return {"status": "BTC_CAPTURE_STOPPED", "cycles": self.state.cycles}


def capture_gap_report() -> dict:
    plan = live_capture_plan()
    desired = list(dict.fromkeys(plan["capture_first"] + plan["capture_high_priority"]))
    missing = [dataset for dataset in desired if dataset not in IMPLEMENTED_DATASETS]
    return {
        "version": "BTC_CAPTURE_GAP_REPORT_V1",
        "implemented_datasets": sorted(IMPLEMENTED_DATASETS),
        "desired_critical_and_high_datasets": desired,
        "missing_collectors": missing,
        "missing_collectors_are_treated_as_neutral": False,
        "missing_collectors_are_reported_unavailable": True,
        "historical_options_fabricated": False,
        "collection_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_CAPTURE_SCHEDULER_CONTRACT_V1",
        "collection_enabled_by_default": False,
        "scheduler_starts_at_import": False,
        "caller_must_explicitly_invoke_service_loop": True,
        "minimum_poll_seconds": 10,
        "implemented_jobs": [COINDCX_FUTURES_RT_JOB],
        "unimplemented_sources_may_be_fabricated": False,
        "missing_source_treated_as_neutral": False,
        "futures_context_capture_enables_futures_execution": False,
        "options_trade_generation_allowed": False,
        "futures_trade_generation_allowed": False,
        "broker_execution_enabled": False,
        "research_only": True,
    }
