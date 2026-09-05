"""Disabled-by-default scheduler for Deribit BTC global options PIT context."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

from app.crypto_deribit_options_pit import DATASET, deribit_options_context_archive_record
from app.deribit_btc_options_context_provider import DeribitBtcOptionsContextProvider


async def _insert(store: Any, record) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("Deribit options PIT store insert_first_seen must return a dict")
    return result


@dataclass(frozen=True)
class DeribitOptionsCapturePolicy:
    enabled: bool = False
    poll_seconds: int = 300

    def validated(self) -> "DeribitOptionsCapturePolicy":
        if int(self.poll_seconds) < 60:
            raise ValueError("Deribit options context poll_seconds must be >= 60")
        return self


class DeribitOptionsPitCaptureScheduler:
    def __init__(
        self,
        *,
        provider: DeribitBtcOptionsContextProvider,
        store: Any,
        policy: DeribitOptionsCapturePolicy | None = None,
    ):
        self.provider = provider
        self.store = store
        self.policy = (policy or DeribitOptionsCapturePolicy()).validated()
        self.cycles = 0
        self.inserted_records = 0
        self.failures = 0

    async def run_cycle(self) -> dict:
        if not self.policy.enabled:
            return {
                "status": "DERIBIT_OPTIONS_CONTEXT_CAPTURE_DISABLED",
                "provider_called": False,
                "store_written": False,
                "captured": [],
                "errors": [],
                "trade_generated": False,
            }

        self.cycles += 1
        try:
            capture = await asyncio.to_thread(self.provider.capture_context)
            record = deribit_options_context_archive_record(capture)
            stored = await _insert(self.store, record)
            status = stored.get("status")
            if status == "INSERTED_FIRST_SEEN":
                self.inserted_records += 1
            captured = [{
                "dataset": DATASET,
                "first_seen_at": capture.first_seen_at.isoformat(),
                "atm_mark_iv_pct": capture.atm_mark_iv_pct,
                "put_call_open_interest_ratio": capture.put_call_open_interest_ratio,
                "term_structure_slope_iv_points": capture.term_structure_slope_iv_points,
                "storage_status": status,
                "global_options_context_only": True,
                "coindcx_contract_selection_allowed": False,
                "coindcx_quote_fill_allowed": False,
                "trade_generated": False,
            }]
            errors = []
        except Exception as exc:
            self.failures += 1
            captured = []
            errors = [{
                "error_type": exc.__class__.__name__,
                "message": str(exc),
                "missing_options_context_treated_as_neutral": False,
            }]

        return {
            "status": "DERIBIT_OPTIONS_CONTEXT_CAPTURE_CYCLE_COMPLETE" if not errors else "DERIBIT_OPTIONS_CONTEXT_CAPTURE_CYCLE_FAILURE",
            "provider_called": True,
            "store_written": any(row["storage_status"] == "INSERTED_FIRST_SEEN" for row in captured),
            "captured": captured,
            "errors": errors,
            "state": {
                "cycles": self.cycles,
                "inserted_records": self.inserted_records,
                "failures": self.failures,
            },
            "trade_generated": False,
        }

    async def run_until_stopped(self, stop_event: asyncio.Event) -> dict:
        if not self.policy.enabled:
            return {"status": "DERIBIT_OPTIONS_CONTEXT_CAPTURE_DISABLED", "cycles": 0}
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.policy.poll_seconds)
            except TimeoutError:
                pass
        return {"status": "DERIBIT_OPTIONS_CONTEXT_CAPTURE_STOPPED", "cycles": self.cycles}


def architecture_contract() -> dict:
    return {
        "version": "DERIBIT_OPTIONS_CONTEXT_CAPTURE_SCHEDULER_V1",
        "collection_enabled_by_default": False,
        "scheduler_starts_at_import": False,
        "minimum_poll_seconds": 60,
        "instrument_metadata_polled_each_cycle": False,
        "global_options_context_only": True,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "missing_options_context_treated_as_neutral": False,
        "underlying_direction_assigned": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
