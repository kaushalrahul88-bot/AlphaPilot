"""User-triggered BTC research actions for the Crypto dashboard.

These endpoints create research/shadow artifacts only. They never accept broker
credentials, place orders, invoke a Futures route, or commit capital.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, Response

from app.crypto_btc_first24h_underlying_backtest import run_first24h_underlying_15m
from app.crypto_btc_live_shadow_click import run_explicit_live_shadow_click
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig

_backtest_lock = asyncio.Lock()
_live_click_lock = asyncio.Lock()


def _database_url(settings) -> str:
    value = str(getattr(settings, "database_url", "") or "").strip()
    if not value:
        raise HTTPException(status_code=503, detail="Crypto research database is unavailable")
    return value


def _request_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"dashboard-{stamp}-{uuid4().hex[:10]}"


def register_crypto_btc_dashboard_action_routes(app, settings) -> None:
    @app.post("/v1/dashboard/crypto/btc/actions/first-24h-underlying-backtest")
    async def run_user_underlying_backtest(response: Response):
        if _backtest_lock.locked():
            raise HTTPException(status_code=409, detail="BTC underlying backtest is already running")
        response.headers["Cache-Control"] = "no-store"
        async with _backtest_lock:
            return await run_first24h_underlying_15m(_database_url(settings))

    @app.post("/v1/dashboard/crypto/btc/actions/live-shadow-click")
    async def generate_user_live_shadow_setup(response: Response):
        if _live_click_lock.locked():
            raise HTTPException(status_code=409, detail="A BTC live shadow setup is already being generated")
        database_url = _database_url(settings)
        response.headers["Cache-Control"] = "no-store"
        async with _live_click_lock:
            try:
                proof_config = BtcProspectiveProofRuntimeConfig.from_env()
                result = await run_explicit_live_shadow_click(
                    request_id=_request_id(),
                    database_url=database_url,
                    proof_config=proof_config,
                )
            except ValueError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "mode": "BTC_DASHBOARD_LIVE_SHADOW_SETUP_V1",
            "status": "GENERATED",
            "research_only": True,
            "order_placed": False,
            "live_execution": False,
            "capital_committed_inr": 0,
            "futures_trade_generated": False,
            "result": result,
        }


def architecture_contract() -> dict:
    return {
        "version": "BTC_DASHBOARD_USER_ACTIONS_CONTRACT_V1",
        "user_backtest_allowed": True,
        "user_live_shadow_setup_allowed": True,
        "broker_order_placement_allowed": False,
        "credentials_accepted_from_browser": False,
        "caller_supplied_decision_time_allowed": False,
        "caller_supplied_request_id_allowed": False,
        "futures_trade_generation_allowed": False,
        "live_execution": False,
        "capital_committed_inr": 0,
    }
