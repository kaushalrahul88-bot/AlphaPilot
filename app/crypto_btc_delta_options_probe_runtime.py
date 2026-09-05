"""Environment-gated Delta India BTC Options public-feed validation loop."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
from typing import Mapping

from app.delta_india_btc_options_public_provider import (
    DeltaIndiaBtcOptionsPublicProvider,
    DeltaIndiaOptionsProbePolicy,
)
from app.crypto_btc_delta_options_probe_postgres import PostgresDeltaIndiaOptionsProbeStore

logger = logging.getLogger("alphapilot.crypto.delta.options.probe")

ENV_ENABLED = "ALPHAPILOT_CRYPTO_DELTA_OPTIONS_PROBE_ENABLED"
ENV_POLL_SECONDS = "ALPHAPILOT_CRYPTO_DELTA_OPTIONS_PROBE_POLL_SECONDS"
ENV_ATM_STRIKES = "ALPHAPILOT_CRYPTO_DELTA_OPTIONS_PROBE_ATM_STRIKES"
ENV_DATABASE_URL = "DATABASE_URL"


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


@dataclass(frozen=True)
class DeltaOptionsProbeRuntimeConfig:
    enabled: bool = False
    database_url: str = ""
    poll_seconds: int = 60
    atm_strikes: int = 7

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DeltaOptionsProbeRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            enabled=_bool(source.get(ENV_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            poll_seconds=_int(source.get(ENV_POLL_SECONDS), 60),
            atm_strikes=_int(source.get(ENV_ATM_STRIKES), 7),
        ).validated()

    def validated(self) -> "DeltaOptionsProbeRuntimeConfig":
        if int(self.poll_seconds) < 30:
            raise ValueError("Delta Options probe poll_seconds must be >= 30")
        DeltaIndiaOptionsProbePolicy(enabled=self.enabled, atm_strikes=self.atm_strikes).validated()
        if self.enabled and not self.database_url:
            raise ValueError("Delta Options probe enabled but DATABASE_URL is missing")
        return self


async def run_delta_options_probe_service(
    config: DeltaOptionsProbeRuntimeConfig,
    *,
    stop_event: asyncio.Event,
    http_client=None,
) -> dict:
    config = config.validated()
    if not config.enabled:
        return {"status": "DELTA_OPTIONS_PROBE_DISABLED", "cycles": 0}

    store = PostgresDeltaIndiaOptionsProbeStore(config.database_url)
    await store.initialize()
    provider = DeltaIndiaBtcOptionsPublicProvider(
        policy=DeltaIndiaOptionsProbePolicy(enabled=True, atm_strikes=config.atm_strikes),
        client=http_client,
    )

    cycles = 0
    inserted = 0
    failures = 0
    while not stop_event.is_set():
        cycles += 1
        try:
            snapshot = await asyncio.to_thread(provider.capture_btc_options_snapshot)
            result = await store.insert_first_seen(snapshot)
            if result["status"] == "INSERTED_FIRST_SEEN":
                inserted += 1
            logger.info(
                "Delta India BTC Options probe captured expiry=%s quotes=%s spot=%s storage=%s",
                snapshot.nearest_expiry.isoformat(),
                len(snapshot.quotes),
                snapshot.reference_spot_price,
                result["status"],
            )
        except Exception:
            failures += 1
            logger.exception("Delta India BTC Options probe cycle failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=config.poll_seconds)
        except TimeoutError:
            pass

    return {
        "status": "DELTA_OPTIONS_PROBE_STOPPED",
        "cycles": cycles,
        "inserted": inserted,
        "failures": failures,
    }


def runtime_status(config: DeltaOptionsProbeRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "DELTA_OPTIONS_PROBE_RUNTIME_STATUS_V1",
        "enabled": config.enabled,
        "database_configured": bool(config.database_url),
        "poll_seconds": config.poll_seconds,
        "atm_strikes": config.atm_strikes,
        "public_market_data_only": True,
        "authentication_required": False,
        "candidate_only": True,
        "options_execution_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "DELTA_OPTIONS_PROBE_RUNTIME_CONTRACT_V1",
        "enabled_by_default": False,
        "minimum_poll_seconds": 30,
        "public_feed_only": True,
        "api_key_required": False,
        "account_access_allowed": False,
        "order_placement_allowed": False,
        "venue_promotion_automatic": False,
        "candidate_only_until_ui_cross_check": True,
        "research_only": True,
    }
