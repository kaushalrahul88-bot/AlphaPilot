"""Render lifecycle registration for the Delta India BTC Options feed probe."""
from __future__ import annotations

import asyncio
import logging

from .crypto_btc_delta_options_probe_runtime import (
    DeltaOptionsProbeRuntimeConfig,
    run_delta_options_probe_service,
)

logger = logging.getLogger("alphapilot.crypto.delta.options.probe")


def register_delta_options_probe_startup(app) -> None:
    state: dict[str, object] = {"stop_event": None, "task": None}

    @app.on_event("startup")
    async def _start_delta_options_probe() -> None:
        config = DeltaOptionsProbeRuntimeConfig.from_env()
        if not config.enabled:
            logger.info("Delta India BTC Options probe disabled")
            return

        stop_event = asyncio.Event()
        task = asyncio.create_task(
            run_delta_options_probe_service(config, stop_event=stop_event),
            name="alphapilot-delta-options-probe",
        )
        state["stop_event"] = stop_event
        state["task"] = task
        logger.info(
            "Delta India BTC Options public-feed probe started poll_seconds=%s atm_strikes=%s",
            config.poll_seconds,
            config.atm_strikes,
        )

    @app.on_event("shutdown")
    async def _stop_delta_options_probe() -> None:
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
