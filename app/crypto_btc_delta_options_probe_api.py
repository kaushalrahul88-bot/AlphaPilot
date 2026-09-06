"""Authenticated one-shot capture API for Delta India BTC Options research data.

This endpoint gives an external scheduler a safe way to wake a sleeping Render
Free service and capture one genuine point-in-time Delta Options snapshot. It
uses the existing collector authentication boundary, public unauthenticated
Delta market data, and the existing immutable candidate-only Postgres store.
It never creates a trade, accesses an account, promotes a venue, or commits
capital.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Header, HTTPException

from app.crypto_btc_delta_options_probe_postgres import PostgresDeltaIndiaOptionsProbeStore
from app.crypto_btc_delta_options_probe_runtime import DeltaOptionsProbeRuntimeConfig
from app.delta_india_btc_options_public_provider import (
    DeltaIndiaBtcOptionsPublicProvider,
    DeltaIndiaOptionsProbePolicy,
)

ROUTE_PATH = "/v1/internal/crypto/btc/delta-options/snapshot-collect"


def _unavailable(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": code,
            "message": message,
            "research_only": True,
            "candidate_only": True,
            "venue_promoted": False,
            "options_trade_generated": False,
            "futures_trade_generated": False,
            "live_execution": False,
            "capital_committed": 0,
        },
    )


async def capture_one_delta_btc_options_snapshot(
    *,
    provider: Any,
    store: Any,
) -> dict[str, Any]:
    """Capture and persist exactly one fresh candidate-only Options snapshot."""
    await store.initialize()
    snapshot = await asyncio.to_thread(provider.capture_btc_options_snapshot)
    persistence = await store.insert_first_seen(snapshot)
    return {
        "status": "DELTA_OPTIONS_SNAPSHOT_CAPTURED",
        "persistence_status": persistence["status"],
        "snapshot_id": persistence["snapshot_id"],
        "first_seen_at": persistence["first_seen_at"],
        "venue": "DELTA_EXCHANGE_INDIA",
        "underlying": "BTC",
        "nearest_expiry": snapshot.nearest_expiry.isoformat(),
        "reference_spot_price": snapshot.reference_spot_price,
        "quote_count": len(snapshot.quotes),
        "selected_strike_count": snapshot.selected_strike_count,
        "full_chain_contract_count": snapshot.full_chain_contract_count,
        "point_in_time_proven": True,
        "candidate_only": True,
        "venue_promoted": False,
        "public_market_data_only": True,
        "api_key_used": False,
        "account_accessed": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
        "research_only": True,
    }


def register_delta_options_probe_routes(app, settings, collector_auth) -> None:
    @app.post(ROUTE_PATH)
    async def collect_delta_btc_options_snapshot(
        x_collector_token: str | None = Header(default=None),
    ):
        # Authenticate before any external market-data request.
        collector_auth(x_collector_token)

        try:
            config = DeltaOptionsProbeRuntimeConfig.from_env()
        except Exception as exc:
            raise _unavailable(
                "DELTA_OPTIONS_CAPTURE_CONFIGURATION_INVALID",
                f"{exc.__class__.__name__}: {str(exc)[:300]}",
            ) from exc

        if not config.enabled:
            raise _unavailable(
                "DELTA_OPTIONS_CAPTURE_DISABLED",
                "Enable the Delta Options research probe before one-shot capture",
            )

        app_database = str(getattr(settings, "database_url", "") or "").strip()
        if not app_database or config.database_url != app_database:
            raise _unavailable(
                "DELTA_OPTIONS_DATABASE_MISMATCH",
                "Delta Options capture and authenticated AlphaPilot must use the same DATABASE_URL",
            )

        provider = DeltaIndiaBtcOptionsPublicProvider(
            policy=DeltaIndiaOptionsProbePolicy(
                enabled=True,
                atm_strikes=config.atm_strikes,
            )
        )
        store = PostgresDeltaIndiaOptionsProbeStore(app_database)
        try:
            return await capture_one_delta_btc_options_snapshot(
                provider=provider,
                store=store,
            )
        except Exception as exc:
            raise _unavailable(
                "DELTA_OPTIONS_CAPTURE_FAILED",
                f"{exc.__class__.__name__}: {str(exc)[:500]}",
            ) from exc


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "DELTA_OPTIONS_ONE_SHOT_CAPTURE_API_CONTRACT_V1",
        "route": ROUTE_PATH,
        "method": "POST",
        "internal_collector_auth_required": True,
        "fresh_public_delta_request_per_invocation": True,
        "database_url_match_required": True,
        "candidate_only": True,
        "venue_promotion_automatic": False,
        "api_key_required": False,
        "account_access_allowed": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
        "research_only": True,
    }
