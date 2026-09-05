"""Protected read-only API for prospective Massive/CME availability evidence.

The route reads only the dedicated operational audit table. It reuses AlphaPilot's
existing internal collector-token authentication, never initializes storage from
a GET request, never calls Massive/CME, and never enables live macro confirmation
or any trading route.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Header, HTTPException

from app.crypto_macro_live_availability_audit import MacroLiveAvailabilityPolicy
from app.crypto_macro_live_availability_postgres import PostgresMacroLiveAvailabilityStore
from app.crypto_macro_live_availability_report import (
    build_macro_live_availability_report_from_store,
    macro_live_availability_report_payload,
)
from app.crypto_macro_live_availability_runtime import MacroLiveAvailabilityRuntimeConfig

ROUTE_PATH = "/v1/internal/crypto/macro-live-availability/report"


def _configuration_unavailable(message: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "MACRO_LIVE_AVAILABILITY_REPORT_DISABLED",
            "message": message,
            "read_only": True,
            "live_confirmation_enabled": False,
            "trade_generation_enabled": False,
        },
    )


def _report_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "MACRO_LIVE_AVAILABILITY_REPORT_UNAVAILABLE",
            "message": f"{exc.__class__.__name__}: {str(exc)[:500]}",
            "read_only": True,
            "provider_network_called": False,
            "store_initialized": False,
            "store_written": False,
            "live_confirmation_enabled": False,
            "trade_generation_enabled": False,
        },
    )


def register_crypto_macro_live_availability_routes(
    app,
    settings,
    collector_auth: Callable[[str | None], Any],
    *,
    store_factory: Callable[[str], Any] = PostgresMacroLiveAvailabilityStore,
    config_loader: Callable[[], MacroLiveAvailabilityRuntimeConfig] = MacroLiveAvailabilityRuntimeConfig.from_env,
) -> None:
    """Register one authenticated read-only operational report endpoint."""

    @app.get(ROUTE_PATH)
    async def crypto_macro_live_availability_report(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)

        try:
            config = config_loader().validated()
        except Exception as exc:
            raise _configuration_unavailable(
                f"macro live-availability report configuration is invalid: {exc.__class__.__name__}: {str(exc)[:300]}"
            ) from exc

        if not config.store_enabled:
            raise _configuration_unavailable(
                "Enable ALPHAPILOT_CRYPTO_MACRO_LIVE_AVAILABILITY_POSTGRES_ENABLED to read persisted audit evidence"
            )
        configured_database = str(config.database_url or "").strip()
        app_database = str(getattr(settings, "database_url", "") or "").strip()
        if not configured_database or configured_database != app_database:
            raise _configuration_unavailable(
                "macro live-availability report database must match the authenticated AlphaPilot DATABASE_URL"
            )

        policy = MacroLiveAvailabilityPolicy(
            max_latency_seconds=config.max_latency_seconds,
            min_unique_events=config.min_unique_events,
        ).validated()

        try:
            store = store_factory(configured_database)
            report = await build_macro_live_availability_report_from_store(
                store,
                policy=policy,
            )
        except Exception as exc:
            raise _report_unavailable(exc) from exc

        payload = macro_live_availability_report_payload(report)
        return {
            **payload,
            "source": "PERSISTED_MACRO_LIVE_AVAILABILITY_AUDIT",
            "database_url_exposed": False,
            "api_key_exposed": False,
            "store_initialized_by_request": False,
            "network_request_performed": False,
        }


def architecture_contract() -> dict:
    return {
        "version": "MASSIVE_MACRO_LIVE_AVAILABILITY_API_V1",
        "route": ROUTE_PATH,
        "method": "GET",
        "internal_collector_auth_required": True,
        "database_url_match_required": True,
        "store_feature_switch_required": True,
        "read_only": True,
        "get_request_may_initialize_store": False,
        "get_request_may_write_store": False,
        "provider_network_call_allowed": False,
        "database_url_exposed": False,
        "api_key_exposed": False,
        "configuration_error_is_empty_history": False,
        "database_error_is_empty_history": False,
        "qualification_auto_enables_live_confirmation": False,
        "live_confirmation_enabled": False,
        "direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
