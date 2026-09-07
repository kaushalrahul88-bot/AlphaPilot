"""Internal API/startup registration for underlying-only F&O V2 validation."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException

from .fno_underlying_prospective_resolver_v2 import resolve_due_underlying_outcomes_v2
from .fno_underlying_prospective_store_v2 import FnoUnderlyingProspectiveStoreV2
from .fno_underlying_prospective_v2 import (
    architecture_contract,
    capture_due_underlying_batch,
    schedule_manifest,
)
from .providers.factory import get_provider

logger = logging.getLogger("alphapilot.fno.underlying_prospective_v2")
IST = ZoneInfo("Asia/Kolkata")


def register_fno_underlying_prospective_v2_routes(app, settings, collector_auth) -> None:
    if getattr(app.state, "fno_underlying_prospective_v2_routes_registered", False):
        return
    app.state.fno_underlying_prospective_v2_routes_registered = True

    @app.post("/v1/internal/fno/underlying-prospective-v2/capture")
    async def capture_underlying_prospective_v2(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        try:
            store = FnoUnderlyingProspectiveStoreV2(settings.database_url)
            await store.initialize()
            return await capture_due_underlying_batch(get_provider(settings), store)
        except Exception as exc:
            logger.exception("Underlying prospective F&O V2 capture failed")
            raise HTTPException(
                status_code=502,
                detail=f"Underlying prospective V2 capture failed: {exc.__class__.__name__}",
            )

    @app.post("/v1/internal/fno/underlying-prospective-v2/resolve")
    async def resolve_underlying_prospective_v2(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        try:
            store = FnoUnderlyingProspectiveStoreV2(settings.database_url)
            await store.initialize()
            return await resolve_due_underlying_outcomes_v2(
                get_provider(settings), store
            )
        except Exception as exc:
            logger.exception("Underlying prospective F&O V2 resolver failed")
            raise HTTPException(
                status_code=502,
                detail=f"Underlying prospective V2 resolver failed: {exc.__class__.__name__}",
            )

    @app.get("/v1/internal/fno/underlying-prospective-v2/status")
    async def underlying_prospective_v2_status(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        store = FnoUnderlyingProspectiveStoreV2(settings.database_url)
        await store.initialize()
        status = await store.status()
        today = datetime.now(timezone.utc).astimezone(IST).date()
        status["today_schedule"] = schedule_manifest(today)
        return status

    @app.get("/v1/internal/fno/underlying-prospective-v2/protocol")
    async def underlying_prospective_v2_protocol(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        today = datetime.now(timezone.utc).astimezone(IST).date()
        return {
            "architecture": architecture_contract(),
            "today_schedule": schedule_manifest(today),
        }


def register_fno_underlying_prospective_v2_startup(app, settings) -> None:
    if getattr(app.state, "fno_underlying_prospective_v2_startup_registered", False):
        return
    app.state.fno_underlying_prospective_v2_startup_registered = True

    @app.on_event("startup")
    async def _initialize_underlying_prospective_v2_schema() -> None:
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            logger.info("Underlying prospective F&O V2 disabled: DATABASE_URL empty")
            return
        try:
            store = FnoUnderlyingProspectiveStoreV2(database_url)
            result = await store.initialize()
            logger.info(
                "Underlying prospective F&O V2 ready immutable=%s live_execution=false",
                result.get("database_immutable"),
            )
        except Exception as exc:
            logger.error(
                "Underlying prospective F&O V2 schema initialization failed: %s: %s",
                exc.__class__.__name__, str(exc)[:300],
            )
