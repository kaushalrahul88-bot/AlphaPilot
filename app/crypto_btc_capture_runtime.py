"""Environment-gated runtime wiring for BTC PIT capture.

Nothing in this module starts automatically. Persistence and collection have
separate explicit switches. CoinGlass derivatives capture has its own opt-in
switch/API key and cannot be activated merely by enabling the base CoinDCX job.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from app.coindcx_btc_public_provider import CoinDcxBtcProviderPolicy, CoinDcxBtcPublicProvider
from app.coinglass_btc_derivatives_provider import CoinGlassBtcDerivativesPolicy, CoinGlassBtcDerivativesProvider
from app.crypto_btc_capture_scheduler import (
    BtcCaptureSchedulerPolicy,
    BtcPitCaptureScheduler,
    COINDCX_FUTURES_RT_JOB,
    COINGLASS_LIQUIDATIONS_JOB,
    COINGLASS_OPEN_INTEREST_JOB,
)
from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore

ENV_ARCHIVE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED"
ENV_CAPTURE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_CAPTURE_ENABLED"
ENV_POLL_SECONDS = "ALPHAPILOT_CRYPTO_BTC_CAPTURE_POLL_SECONDS"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_COINGLASS_ENABLED = "ALPHAPILOT_CRYPTO_BTC_COINGLASS_ENABLED"
ENV_COINGLASS_API_KEY = "COINGLASS_API_KEY"
ENV_COINGLASS_INTERVAL = "ALPHAPILOT_CRYPTO_BTC_COINGLASS_INTERVAL"
ENV_COINGLASS_POLL_SECONDS = "ALPHAPILOT_CRYPTO_BTC_COINGLASS_POLL_SECONDS"


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
class BtcCaptureRuntimeConfig:
    archive_enabled: bool = False
    capture_enabled: bool = False
    database_url: str = ""
    poll_seconds: int = 60
    coinglass_enabled: bool = False
    coinglass_api_key: str = ""
    coinglass_interval: str = "4h"
    coinglass_poll_seconds: int = 300

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BtcCaptureRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            archive_enabled=_bool(source.get(ENV_ARCHIVE_ENABLED), False),
            capture_enabled=_bool(source.get(ENV_CAPTURE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            poll_seconds=_int(source.get(ENV_POLL_SECONDS), 60),
            coinglass_enabled=_bool(source.get(ENV_COINGLASS_ENABLED), False),
            coinglass_api_key=str(source.get(ENV_COINGLASS_API_KEY, "") or "").strip(),
            coinglass_interval=str(source.get(ENV_COINGLASS_INTERVAL, "4h") or "4h").strip(),
            coinglass_poll_seconds=_int(source.get(ENV_COINGLASS_POLL_SECONDS), 300),
        ).validated()

    def validated(self) -> "BtcCaptureRuntimeConfig":
        BtcCaptureSchedulerPolicy(
            enabled=self.capture_enabled,
            poll_seconds=self.poll_seconds,
            coinglass_poll_seconds=self.coinglass_poll_seconds,
        ).validated()
        CoinGlassBtcDerivativesPolicy(
            enabled=self.coinglass_enabled,
            api_key=self.coinglass_api_key,
            interval=self.coinglass_interval,
        ).validated()
        if self.archive_enabled and not self.database_url:
            raise ValueError("BTC PIT Postgres archive enabled but DATABASE_URL is missing")
        if self.capture_enabled and not self.archive_enabled:
            raise ValueError("BTC capture cannot run without the immutable PIT archive enabled")
        if self.capture_enabled and not self.database_url:
            raise ValueError("BTC capture cannot run without DATABASE_URL")
        if self.coinglass_enabled and not self.capture_enabled:
            raise ValueError("CoinGlass BTC capture cannot be enabled while BTC capture is disabled")
        return self


def build_btc_capture_runtime(
    config: BtcCaptureRuntimeConfig,
    *,
    http_client=None,
    coinglass_http_client=None,
) -> dict:
    config = config.validated()
    if not config.archive_enabled:
        return {
            "status": "BTC_PIT_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "provider": None,
            "coinglass_provider": None,
            "scheduler": None,
        }

    store = PostgresBtcPitArchiveStore(config.database_url)
    provider = CoinDcxBtcPublicProvider(
        policy=CoinDcxBtcProviderPolicy(enabled=config.capture_enabled),
        client=http_client,
    )
    coinglass_provider = None
    enabled_jobs = [COINDCX_FUTURES_RT_JOB]
    if config.coinglass_enabled:
        coinglass_provider = CoinGlassBtcDerivativesProvider(
            policy=CoinGlassBtcDerivativesPolicy(
                enabled=True,
                api_key=config.coinglass_api_key,
                interval=config.coinglass_interval,
            ),
            client=coinglass_http_client,
        )
        enabled_jobs.extend([COINGLASS_OPEN_INTEREST_JOB, COINGLASS_LIQUIDATIONS_JOB])

    scheduler = BtcPitCaptureScheduler(
        provider=provider,
        coinglass_provider=coinglass_provider,
        store=store,
        policy=BtcCaptureSchedulerPolicy(
            enabled=config.capture_enabled,
            poll_seconds=config.poll_seconds,
            coinglass_poll_seconds=config.coinglass_poll_seconds,
            enabled_jobs=tuple(enabled_jobs),
        ),
    )
    return {
        "status": "BTC_PIT_RUNTIME_READY" if config.capture_enabled else "BTC_PIT_ARCHIVE_ONLY_READY",
        "config": config,
        "store": store,
        "provider": provider,
        "coinglass_provider": coinglass_provider,
        "scheduler": scheduler,
    }


async def initialize_btc_capture_runtime(config: BtcCaptureRuntimeConfig, *, http_client=None, coinglass_http_client=None) -> dict:
    """Initialize persistence only. This function never starts the scheduler loop."""
    runtime = build_btc_capture_runtime(config, http_client=http_client, coinglass_http_client=coinglass_http_client)
    store = runtime["store"]
    if store is None:
        return {
            "status": "BTC_PIT_RUNTIME_DISABLED",
            "schema_initialized": False,
            "capture_started": False,
        }
    initialized = await store.initialize()
    return {
        "status": runtime["status"],
        "schema_initialized": initialized["status"] == "BTC_PIT_POSTGRES_SCHEMA_READY",
        "capture_started": False,
        "scheduler_enabled": runtime["scheduler"].policy.enabled,
        "coinglass_enabled": config.coinglass_enabled,
    }


async def run_btc_capture_service(config: BtcCaptureRuntimeConfig, *, stop_event, http_client=None, coinglass_http_client=None) -> dict:
    """Explicit service entrypoint; caller must deliberately invoke it."""
    runtime = build_btc_capture_runtime(config, http_client=http_client, coinglass_http_client=coinglass_http_client)
    if runtime["scheduler"] is None:
        return {"status": "BTC_PIT_RUNTIME_DISABLED", "cycles": 0}
    await runtime["store"].initialize()
    return await runtime["scheduler"].run_until_stopped(stop_event)


def runtime_status(config: BtcCaptureRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "BTC_CAPTURE_RUNTIME_STATUS_V2",
        "archive_enabled": config.archive_enabled,
        "capture_enabled": config.capture_enabled,
        "database_configured": bool(config.database_url),
        "poll_seconds": config.poll_seconds,
        "coinglass_enabled": config.coinglass_enabled,
        "coinglass_api_key_configured": bool(config.coinglass_api_key),
        "coinglass_interval": config.coinglass_interval,
        "coinglass_poll_seconds": config.coinglass_poll_seconds,
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "options_execution_enabled": False,
        "futures_execution_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_CAPTURE_RUNTIME_CONTRACT_V2",
        "archive_enabled_by_default": False,
        "capture_enabled_by_default": False,
        "coinglass_enabled_by_default": False,
        "archive_and_capture_switches_are_separate": True,
        "coinglass_has_separate_opt_in": True,
        "coinglass_api_key_required": True,
        "capture_without_archive_allowed": False,
        "capture_without_database_allowed": False,
        "schema_init_starts_capture": False,
        "automatic_startup_registration": False,
        "automatic_network_request": False,
        "options_execution_enabled": False,
        "futures_execution_enabled": False,
        "research_only": True,
    }
