"""Disabled-by-default scheduler for BTC on-chain first-seen capture."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.crypto_btc_onchain_capture import BTC_ONCHAIN_DATASET, glassnode_onchain_archive_record
from app.glassnode_btc_onchain_provider import GlassnodeBtcOnchainProvider


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _insert(store: Any, record) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("on-chain PIT store insert_first_seen must return a dict")
    return result


@dataclass(frozen=True)
class CryptoOnchainCapturePolicy:
    enabled: bool = False
    poll_seconds: int = 600
    continue_after_metric_failure: bool = True

    def validated(self) -> "CryptoOnchainCapturePolicy":
        if int(self.poll_seconds) < 60:
            raise ValueError("crypto on-chain poll_seconds must be >= 60")
        return self


class CryptoOnchainPitCaptureScheduler:
    def __init__(self, *, provider: GlassnodeBtcOnchainProvider, store: Any, policy: CryptoOnchainCapturePolicy | None = None):
        self.provider = provider
        self.store = store
        self.policy = (policy or CryptoOnchainCapturePolicy()).validated()
        self.cycles = 0
        self.inserted_records = 0
        self.idempotent_duplicates = 0
        self.failures = 0

    async def run_cycle(self, *, now: datetime | None = None) -> dict:
        stamp = _utc(now or datetime.now(timezone.utc))
        if not self.policy.enabled:
            return {
                "status": "CRYPTO_ONCHAIN_CAPTURE_DISABLED",
                "provider_called": False,
                "store_written": False,
                "captured": [],
                "errors": [],
                "trade_generated": False,
            }

        self.cycles += 1
        captured = []
        errors = []
        for metric_name in self.provider.policy.metrics:
            try:
                capture = await asyncio.to_thread(self.provider.capture_metric, metric_name, first_seen_at=stamp)
                record = glassnode_onchain_archive_record(capture)
                stored = await _insert(self.store, record)
                status = stored.get("status")
                if status == "INSERTED_FIRST_SEEN":
                    self.inserted_records += 1
                elif status == "IDEMPOTENT_DUPLICATE":
                    self.idempotent_duplicates += 1
                captured.append({
                    "dataset": BTC_ONCHAIN_DATASET,
                    "metric": capture.metric,
                    "provider_time": _utc(capture.provider_time).isoformat(),
                    "first_seen_at": _utc(capture.first_seen_at).isoformat(),
                    "historical_content_immutable": capture.historical_content_immutable,
                    "storage_status": status,
                    "standalone_trade_signal": False,
                    "trade_generated": False,
                })
            except Exception as exc:
                self.failures += 1
                errors.append({
                    "metric": metric_name,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                    "missing_metric_treated_as_neutral": False,
                })
                if not self.policy.continue_after_metric_failure:
                    break

        return {
            "status": "CRYPTO_ONCHAIN_CAPTURE_CYCLE_COMPLETE" if not errors else "CRYPTO_ONCHAIN_CAPTURE_CYCLE_PARTIAL_FAILURE",
            "provider_called": True,
            "store_written": any(row["storage_status"] == "INSERTED_FIRST_SEEN" for row in captured),
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
            return {"status": "CRYPTO_ONCHAIN_CAPTURE_DISABLED", "cycles": 0}
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.policy.poll_seconds)
            except TimeoutError:
                pass
        return {"status": "CRYPTO_ONCHAIN_CAPTURE_STOPPED", "cycles": self.cycles}


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_ONCHAIN_CAPTURE_SCHEDULER_V1",
        "collection_enabled_by_default": False,
        "scheduler_starts_at_import": False,
        "minimum_poll_seconds": 60,
        "pit_and_mutable_provider_metrics_both_preserve_first_seen": True,
        "missing_metric_treated_as_neutral": False,
        "raw_metric_assigns_direction": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
