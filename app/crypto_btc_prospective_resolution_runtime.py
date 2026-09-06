"""Environment-gated automatic resolver for frozen BTC prospective proof decisions.

The resolver only attaches BTC-underlying outcomes after each immutable decision's
pre-frozen ``outcome_due_at``. It never creates a new decision, never changes the
frozen horizon, never uses Options/Futures P&L, and never places an order or
commits capital.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from app.coindcx_btc_public_provider import CoinDcxBtcProviderPolicy, CoinDcxBtcPublicProvider
from app.crypto_btc_prospective_proof_bridge import resolve_prospective_btc_thesis_from_coindcx
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig
from app.crypto_btc_prospective_thesis_postgres import PostgresProspectiveBtcThesisTapeStore

logger = logging.getLogger("alphapilot.crypto.btc.prospective-resolution")

ENV_RESOLUTION_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PROSPECTIVE_RESOLUTION_ENABLED"
ENV_RESOLUTION_POLL_SECONDS = "ALPHAPILOT_CRYPTO_BTC_PROSPECTIVE_RESOLUTION_POLL_SECONDS"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


def _int(value: str | None, default: int) -> int:
    if value is None or not str(value).strip():
        return int(default)
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"invalid integer environment value: {value!r}") from exc


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class BtcProspectiveResolutionRuntimeConfig:
    enabled: bool = False
    poll_seconds: int = 60

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BtcProspectiveResolutionRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            enabled=_bool(source.get(ENV_RESOLUTION_ENABLED), False),
            poll_seconds=_int(source.get(ENV_RESOLUTION_POLL_SECONDS), 60),
        ).validated()

    def validated(self) -> "BtcProspectiveResolutionRuntimeConfig":
        if int(self.poll_seconds) < 30:
            raise ValueError("BTC prospective resolution poll_seconds must be >= 30")
        return self


async def resolve_due_btc_prospective_decisions_once(
    *,
    proof_config: BtcProspectiveProofRuntimeConfig,
    resolution_config: BtcProspectiveResolutionRuntimeConfig,
    now: datetime | None = None,
    store: PostgresProspectiveBtcThesisTapeStore | None = None,
    provider: CoinDcxBtcPublicProvider | None = None,
) -> dict:
    """Resolve every due, still-unresolved BTC proof decision once.

    Outcome attachment is idempotent in the insert-only Postgres tape. A due row
    that cannot yet produce a horizon-valid completed candle remains pending and
    is retried on the next poll; it is never imputed or force-resolved.
    """
    proof = proof_config.validated()
    resolution = resolution_config.validated()
    if not resolution.enabled:
        return {
            "status": "BTC_PROSPECTIVE_RESOLUTION_DISABLED",
            "due_count": 0,
            "resolved_count": 0,
            "unresolved_count": 0,
            "error_count": 0,
        }
    if not proof.postgres_enabled or not proof.database_url:
        raise ValueError("automatic BTC prospective resolution requires enabled Postgres proof persistence")

    resolution_at = _utc(now or datetime.now(timezone.utc))
    tape_store = store or PostgresProspectiveBtcThesisTapeStore(proof.database_url)
    market_provider = provider or CoinDcxBtcPublicProvider(policy=CoinDcxBtcProviderPolicy(enabled=True))
    await tape_store.initialize()
    pending = await tape_store.pending_as_of(resolution_at)

    resolved_count = 0
    unresolved_count = 0
    error_count = 0
    resolved_click_ids: list[str] = []
    unresolved_click_ids: list[str] = []

    for frozen in pending:
        click_id = str(((frozen.get("decision") or {}).get("click_id")) or "")
        try:
            result = await resolve_prospective_btc_thesis_from_coindcx(
                frozen_record=frozen,
                resolution_at=resolution_at,
                provider=market_provider,
            )
            if result.get("status") == "THESIS_OUTCOME_RESOLVED":
                await tape_store.attach_resolution(result)
                resolved_count += 1
                resolved_click_ids.append(click_id)
            else:
                unresolved_count += 1
                unresolved_click_ids.append(click_id)
        except Exception as exc:  # isolate one proof row; never block the rest
            error_count += 1
            unresolved_click_ids.append(click_id)
            logger.error(
                "BTC prospective resolution failed click_id=%s error=%s: %s",
                click_id,
                exc.__class__.__name__,
                str(exc)[:300],
            )

    return {
        "status": "BTC_PROSPECTIVE_RESOLUTION_PASS_COMPLETE",
        "resolution_at": resolution_at.isoformat(),
        "due_count": len(pending),
        "resolved_count": resolved_count,
        "unresolved_count": unresolved_count,
        "error_count": error_count,
        "resolved_click_ids": resolved_click_ids,
        "unresolved_click_ids": unresolved_click_ids,
        "options_pnl_measured": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
    }


async def run_btc_prospective_resolution_service(
    proof_config: BtcProspectiveProofRuntimeConfig,
    resolution_config: BtcProspectiveResolutionRuntimeConfig,
    *,
    stop_event: asyncio.Event,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Poll only for due unresolved decisions and attach BTC-only outcomes."""
    resolution_config.validated()
    if not resolution_config.enabled:
        return
    while not stop_event.is_set():
        try:
            await resolve_due_btc_prospective_decisions_once(
                proof_config=proof_config,
                resolution_config=resolution_config,
                now=(clock or (lambda: datetime.now(timezone.utc)))(),
            )
        except Exception as exc:
            logger.error(
                "BTC prospective resolution pass failed error=%s: %s",
                exc.__class__.__name__,
                str(exc)[:300],
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=int(resolution_config.poll_seconds))
        except TimeoutError:
            continue


def architecture_contract() -> dict:
    return {
        "version": "BTC_PROSPECTIVE_RESOLUTION_RUNTIME_CONTRACT_V1",
        "enabled_by_default": False,
        "minimum_poll_seconds": 30,
        "creates_decisions": False,
        "changes_frozen_horizon": False,
        "resolves_only_due_unresolved_decisions": True,
        "outcome_source": "COINDCX_PUBLIC_COMPLETED_SPOT_CANDLES",
        "unresolved_outcomes_are_imputed": False,
        "options_pnl_measured": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
        "research_only": True,
    }
