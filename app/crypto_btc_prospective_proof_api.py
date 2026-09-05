"""Authenticated one-shot API for prospective BTC underlying proof decisions.

The route is deliberately explicit and server-time-only. A caller cannot provide
``decision_at`` or reconstruct a historical click. Each POST reads the already
running immutable BTC PIT archive plus completed CoinDCX spot candles, freezes
one underlying thesis, and persists it only when the proof bridge produced a
valid frozen decision. It never resolves outcomes or touches Options/Futures
execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import Header, HTTPException

from app.crypto_btc_capture_runtime import BtcCaptureRuntimeConfig, build_btc_capture_runtime
from app.crypto_btc_prospective_proof_bridge import freeze_prospective_btc_thesis_from_existing_sources
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig
from app.crypto_btc_prospective_thesis_postgres import PostgresProspectiveBtcThesisTapeStore

ROUTE_PATH = "/v1/internal/crypto/btc/prospective-thesis/freeze"


def _unavailable(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": code,
            "message": message,
            "research_only": True,
            "historical_backfill_allowed": False,
            "automatic_decision_scheduler": False,
            "automatic_outcome_resolution": False,
            "options_trade_generated": False,
            "futures_trade_generated": False,
            "live_execution": False,
            "capital_committed": 0,
        },
    )


def _click_id(decision_at: datetime) -> str:
    stamp = decision_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"btc-proof-{stamp}-{uuid4().hex[:10]}"


async def freeze_one_prospective_btc_thesis(
    *,
    decision_at: datetime,
    click_id: str,
    provider: Any,
    pit_store: Any,
    thesis_store: Any,
    tape_policy,
    freeze_func: Callable[..., Awaitable[dict]] = freeze_prospective_btc_thesis_from_existing_sources,
) -> dict:
    """Freeze and persist exactly one decision; unresolved inputs are not stored."""
    result = await freeze_func(
        click_id=click_id,
        decision_at=decision_at,
        provider=provider,
        pit_store=pit_store,
        tape_policy=tape_policy,
    )
    frozen = result.get("frozen_thesis") if isinstance(result, dict) else None
    if result.get("status") != "PROSPECTIVE_PROOF_DECISION_FROZEN" or not isinstance(frozen, dict):
        return {
            **result,
            "decision_persisted": False,
            "persistence_status": "NOT_PERSISTED_UNRESOLVED_INPUT",
            "automatic_outcome_resolution": False,
            "trade_generated": False,
        }

    persistence = await thesis_store.insert_frozen(frozen)
    return {
        **result,
        "decision_persisted": True,
        "persistence_status": persistence["status"],
        "persisted_click_id": persistence["click_id"],
        "persisted_tape_fingerprint": persistence["tape_fingerprint"],
        "automatic_outcome_resolution": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
    }


def register_btc_prospective_proof_routes(app, settings, collector_auth) -> None:
    @app.post(ROUTE_PATH)
    async def freeze_btc_prospective_thesis(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)

        try:
            proof_config = BtcProspectiveProofRuntimeConfig.from_env()
            capture_config = BtcCaptureRuntimeConfig.from_env()
        except Exception as exc:
            raise _unavailable(
                "BTC_PROSPECTIVE_PROOF_CONFIGURATION_INVALID",
                f"{exc.__class__.__name__}: {str(exc)[:300]}",
            ) from exc

        if not proof_config.postgres_enabled:
            raise _unavailable(
                "BTC_PROSPECTIVE_PROOF_DISABLED",
                "Enable ALPHAPILOT_CRYPTO_BTC_PROSPECTIVE_THESIS_POSTGRES_ENABLED before freezing proof decisions",
            )
        if not capture_config.archive_enabled or not capture_config.capture_enabled:
            raise _unavailable(
                "BTC_PROSPECTIVE_INPUT_CAPTURE_DISABLED",
                "Prospective proof requires the immutable BTC PIT archive and live research capture to be enabled",
            )

        app_database = str(getattr(settings, "database_url", "") or "").strip()
        if (
            not app_database
            or proof_config.database_url != app_database
            or capture_config.database_url != app_database
        ):
            raise _unavailable(
                "BTC_PROSPECTIVE_DATABASE_MISMATCH",
                "BTC PIT capture, prospective proof, and authenticated AlphaPilot must use the same DATABASE_URL",
            )

        runtime = build_btc_capture_runtime(capture_config)
        provider = runtime.get("provider")
        pit_store = runtime.get("store")
        if provider is None or pit_store is None:
            raise _unavailable(
                "BTC_PROSPECTIVE_INPUT_RUNTIME_UNAVAILABLE",
                "BTC prospective input runtime is unavailable",
            )

        decision_at = datetime.now(timezone.utc)
        click_id = _click_id(decision_at)
        thesis_store = PostgresProspectiveBtcThesisTapeStore(app_database)
        try:
            return await freeze_one_prospective_btc_thesis(
                decision_at=decision_at,
                click_id=click_id,
                provider=provider,
                pit_store=pit_store,
                thesis_store=thesis_store,
                tape_policy=proof_config.tape_policy(),
            )
        except Exception as exc:
            raise _unavailable(
                "BTC_PROSPECTIVE_FREEZE_FAILED",
                f"{exc.__class__.__name__}: {str(exc)[:500]}",
            ) from exc


def architecture_contract() -> dict:
    return {
        "version": "BTC_PROSPECTIVE_PROOF_API_CONTRACT_V1",
        "route": ROUTE_PATH,
        "method": "POST",
        "internal_collector_auth_required": True,
        "server_time_only": True,
        "caller_supplied_decision_at_allowed": False,
        "historical_backfill_allowed": False,
        "pit_archive_required": True,
        "live_research_capture_required": True,
        "database_url_match_required": True,
        "explicit_invocation_required": True,
        "automatic_decision_scheduler": False,
        "automatic_outcome_resolution": False,
        "unresolved_input_persisted_as_decision": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
        "research_only": True,
    }
