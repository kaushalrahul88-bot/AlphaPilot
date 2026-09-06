"""Authenticated internal route for the read-only F&O expiry-history probe."""
from __future__ import annotations

import logging

from fastapi import Header, HTTPException

from .fno_current_expiry_history_probe import probe_current_expiry_history
from .providers.factory import get_provider

logger = logging.getLogger("alphapilot.fno.current_expiry_history_probe")


def register_fno_current_expiry_history_probe_routes(app, settings, collector_auth) -> None:
    if getattr(app.state, "fno_current_expiry_history_probe_registered", False):
        return
    app.state.fno_current_expiry_history_probe_registered = True

    @app.get("/v1/internal/fno/current-expiry-history/probe")
    async def current_expiry_history_probe(
        underlying: str,
        expiry_date: str,
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        try:
            return await probe_current_expiry_history(
                get_provider(settings),
                underlying=underlying,
                expiry_date=expiry_date,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("F&O current-expiry history probe failed")
            raise HTTPException(
                status_code=502,
                detail=f"F&O current-expiry history probe failed: {exc.__class__.__name__}: {str(exc)[:320]}",
            )


def architecture_contract() -> dict:
    return {
        "version": "FNO_CURRENT_EXPIRY_HISTORY_PROBE_API_V1",
        "collector_auth_required": True,
        "read_only": True,
        "live_execution": False,
        "capital_committed": 0,
    }
