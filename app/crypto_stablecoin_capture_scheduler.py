"""Disabled-by-default scheduler for aggregate stablecoin supply PIT capture."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.crypto_stablecoin_pit_capture import STABLECOIN_SUPPLY_DATASET, defillama_stablecoin_archive_record
from app.defillama_stablecoin_provider import DefiLlamaStablecoinProvider


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _insert(store: Any, record) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("stablecoin PIT store insert_first_seen must return a dict")
    return result


@dataclass(frozen=True)
class StablecoinSupplyCapturePolicy:
    enabled: bool = False
    poll_seconds: int = 3600

    def validated(self) -> "StablecoinSupplyCapturePolicy":
        if int(self.poll_seconds) < 300:
            raise ValueError("stablecoin supply poll_seconds must be >= 300")
        return self


class StablecoinSupplyPitCaptureScheduler:
    def __init__(self, *, provider: DefiLlamaStablecoinProvider, store: Any, policy: StablecoinSupplyCapturePolicy | None = None):
        self.provider = provider
        self.store = store
        self.policy = (policy or StablecoinSupplyCapturePolicy()).validated()
        self.cycles = 0
        self.inserted_records = 0
        self.idempotent_duplicates = 0
        self.failures = 0

    async def run_cycle(self, *, now: datetime | None = None) -> dict:
        stamp = _utc(now or datetime.now(timezone.utc))
        if not self.policy.enabled:
            return {
                "status": "STABLECOIN_SUPPLY_CAPTURE_DISABLED",
                "provider_called": False,
                "store_written": False,
                "captured": [],
                "errors": [],
                "trade_generated": False,
            }

        self.cycles += 1
        try:
            capture = await asyncio.to_thread(self.provider.capture_supply, first_seen_at=stamp)
            record = defillama_stablecoin_archive_record(capture)
            stored = await _insert(self.store, record)
            status = stored.get("status")
            if status == "INSERTED_FIRST_SEEN":
                self.inserted_records += 1
            elif status == "IDEMPOTENT_DUPLICATE":
                self.idempotent_duplicates += 1
            captured = [{
                "dataset": STABLECOIN_SUPPLY_DATASET,
                "first_seen_at": stamp.isoformat(),
                "total_circulating": capture.total_circulating,
                "asset_count": capture.asset_count,
                "storage_status": status,
                "aggregate_supply_equals_exchange_inflow": False,
                "direction_assigned": False,
                "trade_generated": False,
            }]
            errors = []
        except Exception as exc:
            self.failures += 1
            captured = []
            errors = [{
                "error_type": exc.__class__.__name__,
                "message": str(exc),
                "missing_supply_treated_as_neutral": False,
            }]

        return {
            "status": "STABLECOIN_SUPPLY_CAPTURE_CYCLE_COMPLETE" if not errors else "STABLECOIN_SUPPLY_CAPTURE_CYCLE_FAILURE",
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
            return {"status": "STABLECOIN_SUPPLY_CAPTURE_DISABLED", "cycles": 0}
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.policy.poll_seconds)
            except TimeoutError:
                pass
        return {"status": "STABLECOIN_SUPPLY_CAPTURE_STOPPED", "cycles": self.cycles}


def architecture_contract() -> dict:
    return {
        "version": "STABLECOIN_SUPPLY_CAPTURE_SCHEDULER_V1",
        "collection_enabled_by_default": False,
        "scheduler_starts_at_import": False,
        "minimum_poll_seconds": 300,
        "aggregate_supply_only": True,
        "venue_specific_exchange_flow_captured": False,
        "missing_supply_treated_as_neutral": False,
        "direction_assigned": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
