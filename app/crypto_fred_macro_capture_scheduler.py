"""Disabled-by-default scheduler for prospective FRED BTC macro-regime PIT capture."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

from app.crypto_fred_macro_pit import DATASET, fred_macro_live_archive_record
from app.fred_btc_macro_regime_provider import FredBtcMacroRegimeProvider


async def _insert(store: Any, record) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("FRED macro PIT store insert_first_seen must return a dict")
    return result


@dataclass(frozen=True)
class FredMacroCapturePolicy:
    enabled: bool = False
    poll_seconds: int = 3600

    def validated(self) -> "FredMacroCapturePolicy":
        if int(self.poll_seconds) < 900:
            raise ValueError("FRED macro poll_seconds must be >= 900")
        return self


class FredMacroPitCaptureScheduler:
    def __init__(
        self,
        *,
        provider: FredBtcMacroRegimeProvider,
        store: Any,
        policy: FredMacroCapturePolicy | None = None,
    ):
        self.provider = provider
        self.store = store
        self.policy = (policy or FredMacroCapturePolicy()).validated()
        self.cycles = 0
        self.inserted_records = 0
        self.idempotent_duplicates = 0
        self.failures = 0

    async def run_cycle(self) -> dict:
        if not self.policy.enabled:
            return {
                "status": "FRED_MACRO_CAPTURE_DISABLED",
                "provider_called": False,
                "store_written": False,
                "captured": [],
                "errors": [],
                "trade_generated": False,
            }

        self.cycles += 1
        try:
            capture = await asyncio.to_thread(self.provider.capture_regime)
            record = fred_macro_live_archive_record(capture)
            stored = await _insert(self.store, record)
            storage_status = stored.get("status")
            if storage_status == "INSERTED_FIRST_SEEN":
                self.inserted_records += 1
            elif storage_status == "IDEMPOTENT_DUPLICATE":
                self.idempotent_duplicates += 1
            else:
                raise ValueError(f"unexpected FRED macro PIT storage status: {storage_status!r}")
            captured = [{
                "dataset": DATASET,
                "vintage_date": capture.vintage_date.isoformat(),
                "first_seen_at": capture.first_seen_at.isoformat(),
                "storage_status": storage_status,
                "historical_vintage_reconstruction": False,
                "daily_regime_context_only": True,
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
                "missing_macro_treated_as_neutral": False,
            }]

        return {
            "status": "FRED_MACRO_CAPTURE_CYCLE_COMPLETE" if not errors else "FRED_MACRO_CAPTURE_CYCLE_FAILURE",
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
            return {"status": "FRED_MACRO_CAPTURE_DISABLED", "cycles": 0, "trade_generated": False}
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=int(self.policy.poll_seconds))
            except TimeoutError:
                pass
        return {"status": "FRED_MACRO_CAPTURE_STOPPED", "cycles": self.cycles, "trade_generated": False}


def architecture_contract() -> dict:
    return {
        "version": "FRED_BTC_MACRO_CAPTURE_SCHEDULER_V1",
        "collection_enabled_by_default": False,
        "scheduler_starts_at_import": False,
        "minimum_poll_seconds": 900,
        "provider_first_seen_controls_visibility": True,
        "historical_reconstruction_performed_by_live_scheduler": False,
        "same_day_first_seen_archive_only": True,
        "missing_macro_treated_as_neutral": False,
        "direction_assigned": False,
        "may_supply_second_intraday_origin": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
