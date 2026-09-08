"""Lifecycle wiring for independently gated BTC context collectors.

Each collector keeps its own environment switch. Registration starts no network
activity unless that collector validates as enabled, and no collector can create
an order or enable a trade route.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.crypto_deribit_options_capture_runtime import (
    DeribitOptionsRuntimeConfig,
    run_deribit_options_service,
)
from app.crypto_stablecoin_capture_runtime import (
    StablecoinSupplyRuntimeConfig,
    run_stablecoin_supply_service,
)

logger = logging.getLogger("alphapilot.crypto.btc.context-capture")


@dataclass(frozen=True)
class _Collector:
    name: str
    enabled: Callable[[Any], bool]
    load: Callable[[], Any]
    run: Callable[..., Awaitable[dict]]


COLLECTORS = (
    _Collector(
        name="deribit-options-context",
        load=DeribitOptionsRuntimeConfig.from_env,
        enabled=lambda config: bool(config.archive_enabled and config.deribit_enabled),
        run=run_deribit_options_service,
    ),
    _Collector(
        name="defillama-stablecoin-supply",
        load=StablecoinSupplyRuntimeConfig.from_env,
        enabled=lambda config: bool(config.archive_enabled and config.stablecoin_enabled),
        run=run_stablecoin_supply_service,
    ),
)


def register_btc_context_capture_startup(app) -> None:
    state: dict[str, tuple[asyncio.Event, asyncio.Task]] = {}

    @app.on_event("startup")
    async def _start_btc_context_collectors() -> None:
        for collector in COLLECTORS:
            try:
                config = collector.load()
            except Exception as exc:
                logger.error(
                    "BTC context collector configuration invalid name=%s error=%s: %s",
                    collector.name, exc.__class__.__name__, str(exc)[:500],
                )
                continue
            if not collector.enabled(config):
                logger.info("BTC context collector disabled name=%s", collector.name)
                continue
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                collector.run(config, stop_event=stop_event),
                name=f"alphapilot-btc-{collector.name}",
            )
            state[collector.name] = (stop_event, task)
            logger.info("BTC context collector started name=%s", collector.name)

    @app.on_event("shutdown")
    async def _stop_btc_context_collectors() -> None:
        for stop_event, _ in state.values():
            stop_event.set()
        tasks = [task for _, task in state.values()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=10)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        state.clear()


def architecture_contract() -> dict:
    return {
        "version": "BTC_CONTEXT_CAPTURE_STARTUP_V1",
        "collectors": [collector.name for collector in COLLECTORS],
        "independent_environment_gates": True,
        "disabled_collector_performs_network_request": False,
        "collector_failure_enables_other_collector": False,
        "options_context_may_select_delta_contract": False,
        "futures_trade_generation_enabled": False,
        "live_execution": False,
        "research_only": True,
    }
