"""Internal API/startup registration for underlying-only F&O edge validation."""
from __future__ import annotations

import logging

from fastapi import Header, HTTPException

from .fno_underlying_prospective_resolver_v1 import resolve_due_underlying_outcomes
from .fno_underlying_prospective_store_v1 import FnoUnderlyingProspectiveStore
from .fno_underlying_prospective_v1 import (
    architecture_contract,
    capture_due_underlying_batch,
    deterministic_clicks,
)
from .providers.factory import get_provider

logger = logging.getLogger("alphapilot.fno.underlying_prospective")


def register_fno_underlying_prospective_routes(app, settings, collector_auth) -> None:
    if getattr(app.state, "fno_underlying_prospective_routes_registered", False):
        return
    app.state.fno_underlying_prospective_routes_registered = True

    @app.post("/v1/internal/fno/underlying-prospective/capture")
    async def capture_underlying_prospective(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        try:
            store = FnoUnderlyingProspectiveStore(settings.database_url)
            await store.initialize()
            return await capture_due_underlying_batch(get_provider(settings), store)
        except Exception as exc:
            logger.exception("Underlying prospective F&O capture failed")
            raise HTTPException(
                status_code=502,
                detail=f"Underlying prospective capture failed: {exc.__class__.__name__}",
            )

    @app.post("/v1/internal/fno/underlying-prospective/resolve")
    async def resolve_underlying_prospective(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        try:
            store = FnoUnderlyingProspectiveStore(settings.database_url)
            await store.initialize()
            return await resolve_due_underlying_outcomes(get_provider(settings), store)
        except Exception as exc:
            logger.exception("Underlying prospective F&O resolver failed")
            raise HTTPException(
                status_code=502,
                detail=f"Underlying prospective resolver failed: {exc.__class__.__name__}",
            )

    @app.get("/v1/internal/fno/underlying-prospective/status")
    async def underlying_prospective_status(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        store = FnoUnderlyingProspectiveStore(settings.database_url)
        await store.initialize()
        return await store.status()

    @app.get("/v1/internal/fno/underlying-prospective/protocol")
    async def underlying_prospective_protocol(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
        return {
            "architecture": architecture_contract(),
            "today_clicks_ist": [
                item.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M")
                for item in deterministic_clicks(today)
            ],
        }


def register_fno_underlying_prospective_startup(app, settings) -> None:
    if getattr(app.state, "fno_underlying_prospective_startup_registered", False):
        return
    app.state.fno_underlying_prospective_startup_registered = True

    @app.on_event("startup")
    async def _initialize_underlying_prospective_schema() -> None:
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            logger.info("Underlying prospective F&O store disabled: DATABASE_URL empty")
            return
        try:
            store = FnoUnderlyingProspectiveStore(database_url)
            result = await store.initialize()
            logger.info(
                "Underlying prospective F&O store ready immutable=%s live_execution=false",
                result.get("database_immutable"),
            )
        except Exception as exc:
            logger.error(
                "Underlying prospective F&O schema initialization failed: %s: %s",
                exc.__class__.__name__, str(exc)[:300],
            )
