"""Render lifecycle registration for BTC prospective outcome resolution."""
from __future__ import annotations

import asyncio
import logging

from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig
from app.crypto_btc_prospective_resolution_runtime import (
    BtcProspectiveResolutionRuntimeConfig,
    run_btc_prospective_resolution_service,
)

logger = logging.getLogger("alphapilot.crypto.btc.prospective-resolution")


def register_btc_prospective_resolution_startup(app, settings) -> None:
    state: dict[str, object] = {"stop_event": None, "task": None}

    @app.on_event("startup")
    async def _start_btc_prospective_resolution() -> None:
        try:
            proof_config = BtcProspectiveProofRuntimeConfig.from_env()
            resolution_config = BtcProspectiveResolutionRuntimeConfig.from_env()
        except Exception as exc:
            logger.error(
                "BTC prospective resolution configuration invalid: %s: %s",
                exc.__class__.__name__,
                str(exc)[:300],
            )
            return

        if not resolution_config.enabled:
            logger.info("BTC prospective automatic resolution disabled")
            return
        if not proof_config.postgres_enabled:
            logger.error("BTC prospective automatic resolution requires proof Postgres persistence")
            return

        app_database = str(getattr(settings, "database_url", "") or "").strip()
        if not app_database or proof_config.database_url != app_database:
            logger.error("BTC prospective resolution database does not match AlphaPilot DATABASE_URL")
            return

        stop_event = asyncio.Event()
        task = asyncio.create_task(
            run_btc_prospective_resolution_service(
                proof_config,
                resolution_config,
                stop_event=stop_event,
            ),
            name="alphapilot-btc-prospective-resolution",
        )
        state["stop_event"] = stop_event
        state["task"] = task
        logger.info(
            "BTC prospective automatic resolution started poll_seconds=%s horizon_hours=%s research_only=true",
            resolution_config.poll_seconds,
            proof_config.evaluation_horizon_hours,
        )

    @app.on_event("shutdown")
    async def _stop_btc_prospective_resolution() -> None:
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
