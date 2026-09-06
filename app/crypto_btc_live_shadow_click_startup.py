"""Render startup hook for an explicit one-shot BTC Options shadow click.

A click happens only when a non-empty request ID is deliberately supplied in the
environment. The request ID is immutable/idempotent in Postgres, so ordinary
future restarts with the same environment value do not create new samples.
"""
from __future__ import annotations

import logging
import os

from app.crypto_btc_live_shadow_click import run_explicit_live_shadow_click
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig

logger = logging.getLogger("alphapilot.crypto.btc.live-shadow-click")

ENV_REQUEST_ID = "ALPHAPILOT_CRYPTO_BTC_LIVE_SHADOW_CLICK_REQUEST_ID"


def register_btc_live_shadow_click_startup(app, settings) -> None:
    @app.on_event("startup")
    async def _run_requested_btc_live_shadow_click() -> None:
        request_id = str(os.getenv(ENV_REQUEST_ID, "") or "").strip()
        if not request_id:
            logger.info("BTC Options live shadow click not requested")
            return

        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            logger.error("BTC Options live shadow click requested but DATABASE_URL is unavailable")
            return
        try:
            proof_config = BtcProspectiveProofRuntimeConfig.from_env()
            if not proof_config.postgres_enabled or proof_config.database_url != database_url:
                raise ValueError("prospective BTC proof store is not enabled on the AlphaPilot database")
            result = await run_explicit_live_shadow_click(
                request_id=request_id,
                database_url=database_url,
                proof_config=proof_config,
            )
        except Exception as exc:
            logger.error(
                "BTC Options live shadow click failed request_id=%s error=%s: %s",
                request_id,
                exc.__class__.__name__,
                str(exc)[:500],
            )
            return

        if result.get("status") == "LIVE_SHADOW_CLICK_ALREADY_FROZEN":
            logger.info("BTC Options live shadow click already frozen request_id=%s", request_id)
            return
        logger.info(
            "BTC Options live shadow click frozen request_id=%s status=%s direction=%s reason=%s",
            request_id,
            result.get("shadow_status"),
            result.get("market_direction"),
            result.get("reason"),
        )


def architecture_contract() -> dict:
    return {
        "version": "BTC_OPTIONS_LIVE_SHADOW_CLICK_STARTUP_CONTRACT_V1",
        "request_id_required": True,
        "automatic_recurring_clicks": False,
        "same_request_id_creates_second_click": False,
        "caller_supplied_decision_at": False,
        "live_execution": False,
        "research_only": True,
    }
