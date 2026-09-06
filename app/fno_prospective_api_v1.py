"""Internal API/startup registration for prospective NSE F&O learning."""
from __future__ import annotations

import logging
from typing import Iterable

from fastapi import Header, HTTPException

from .fno_prospective_capture_v1 import capture_prospective_batch
from .fno_prospective_protocol_v1 import MAX_BATCH_SIZE, protocol_manifest
from .fno_prospective_resolver_v1 import resolve_due_fno_episodes
from .fno_prospective_store_v1 import FnoProspectiveStore
from .fno_selected_contract_tape_v1 import collect_selected_contract_observations
from .providers.factory import get_provider

logger = logging.getLogger("alphapilot.fno.prospective")


def _symbols(value: str | None) -> Iterable[str] | None:
    if value is None:
        return None
    items = [item.strip().upper() for item in value.split(",") if item.strip()]
    return items or None


def register_fno_prospective_routes(app, settings, collector_auth) -> None:
    if getattr(app.state, "fno_prospective_routes_registered", False):
        return
    app.state.fno_prospective_routes_registered = True

    @app.post("/v1/internal/fno/prospective/capture")
    async def fno_prospective_capture(
        batch_size: int = 4,
        symbols: str | None = None,
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
            raise HTTPException(status_code=400, detail=f"batch_size must be 1-{MAX_BATCH_SIZE}")
        try:
            store = FnoProspectiveStore(settings.database_url)
            await store.initialize()
            return await capture_prospective_batch(
                get_provider(settings),
                store,
                batch_size=batch_size,
                symbols=_symbols(symbols),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("F&O prospective capture failed")
            raise HTTPException(status_code=502, detail=f"F&O prospective capture failed: {exc.__class__.__name__}")

    @app.post("/v1/internal/fno/selected-contracts/collect")
    async def fno_selected_contract_collect(
        limit: int = 24,
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        if limit < 1 or limit > 100:
            raise HTTPException(status_code=400, detail="limit must be 1-100")
        try:
            store = FnoProspectiveStore(settings.database_url)
            await store.initialize()
            return await collect_selected_contract_observations(
                get_provider(settings), store, limit=limit
            )
        except Exception as exc:
            logger.exception("F&O selected-contract collection failed")
            raise HTTPException(status_code=502, detail=f"F&O selected-contract collection failed: {exc.__class__.__name__}")

    @app.post("/v1/internal/fno/prospective/resolve")
    async def fno_prospective_resolve(
        limit: int = 50,
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        if limit < 1 or limit > 500:
            raise HTTPException(status_code=400, detail="limit must be 1-500")
        try:
            store = FnoProspectiveStore(settings.database_url)
            await store.initialize()
            return await resolve_due_fno_episodes(
                get_provider(settings), store, limit=limit
            )
        except Exception as exc:
            logger.exception("F&O prospective outcome resolver failed")
            raise HTTPException(status_code=502, detail=f"F&O prospective resolver failed: {exc.__class__.__name__}")

    @app.get("/v1/internal/fno/prospective/status")
    async def fno_prospective_status(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        store = FnoProspectiveStore(settings.database_url)
        await store.initialize()
        return await store.status()

    @app.get("/v1/internal/fno/prospective/protocol")
    async def fno_prospective_protocol(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        return protocol_manifest()


def register_fno_prospective_startup(app, settings) -> None:
    if getattr(app.state, "fno_prospective_startup_registered", False):
        return
    app.state.fno_prospective_startup_registered = True

    @app.on_event("startup")
    async def _initialize_fno_prospective_schema() -> None:
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            logger.info("F&O prospective store disabled: DATABASE_URL is empty")
            return
        try:
            store = FnoProspectiveStore(database_url)
            result = await store.initialize()
            logger.info(
                "F&O prospective store ready immutable=%s automatic_capture=false automatic_resolution=false live_execution=false",
                result.get("database_immutable"),
            )
        except Exception as exc:
            logger.error(
                "F&O prospective schema initialization failed: %s: %s",
                exc.__class__.__name__,
                str(exc)[:300],
            )


def architecture_contract() -> dict:
    return {
        "version": "FNO_PROSPECTIVE_INTERNAL_API_V1",
        "collector_token_required": True,
        "startup_initializes_schema_only": True,
        "startup_starts_collection": False,
        "startup_starts_decisions": False,
        "startup_starts_resolver": False,
        "live_execution": False,
        "capital_committed": 0,
    }
