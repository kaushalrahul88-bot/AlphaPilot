"""Render lifecycle registration for research-only BTC PIT capture.

The lifecycle remains fail-closed and environment gated. Merely deploying this
module performs no market request: collection starts only when both immutable
archive and capture switches validate successfully.
"""
from __future__ import annotations

import asyncio
import logging

from .crypto_btc_capture_runtime import BtcCaptureRuntimeConfig, run_btc_capture_service

logger = logging.getLogger("alphapilot.crypto.btc.capture")


def register_btc_capture_startup(app) -> None:
    state: dict[str, object] = {"stop_event": None, "task": None}

    @app.on_event("startup")
    async def _start_btc_pit_capture() -> None:
        config = BtcCaptureRuntimeConfig.from_env()
        if not (config.archive_enabled and config.capture_enabled):
            logger.info(
                "BTC PIT capture disabled archive_enabled=%s capture_enabled=%s",
                config.archive_enabled,
                config.capture_enabled,
            )
            return

        stop_event = asyncio.Event()
        task = asyncio.create_task(
            run_btc_capture_service(config, stop_event=stop_event),
            name="alphapilot-btc-pit-capture",
        )
        state["stop_event"] = stop_event
        state["task"] = task
        logger.info(
            "BTC PIT research capture started poll_seconds=%s coinglass_enabled=%s",
            config.poll_seconds,
            config.coinglass_enabled,
        )

    @app.on_event("shutdown")
    async def _stop_btc_pit_capture() -> None:
        stop_event = state.get("stop_event")
        task = state.get("task")
        if isinstance(stop_event, asyncio.Event):
            stop_event.set()
        if isinstance(task, asyncio.Task):
            try:
                await asyncio.wait_for(task, timeout=10)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        state["stop_event"] = None
        state["task"] = None
