"""Environment-gated runtime for BTC Glassnode on-chain PIT capture."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_onchain_capture_scheduler import CryptoOnchainCapturePolicy, CryptoOnchainPitCaptureScheduler
from app.glassnode_btc_onchain_provider import GlassnodeBtcOnchainPolicy, GlassnodeBtcOnchainProvider, METRICS

ENV_ARCHIVE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_GLASSNODE_ENABLED = "ALPHAPILOT_CRYPTO_GLASSNODE_ENABLED"
ENV_GLASSNODE_API_KEY = "GLASSNODE_API_KEY"
ENV_GLASSNODE_POLL_SECONDS = "ALPHAPILOT_CRYPTO_GLASSNODE_POLL_SECONDS"
ENV_GLASSNODE_INTERVAL = "ALPHAPILOT_CRYPTO_GLASSNODE_INTERVAL"
ENV_GLASSNODE_LOOKBACK_HOURS = "ALPHAPILOT_CRYPTO_GLASSNODE_LOOKBACK_HOURS"
ENV_GLASSNODE_METRICS = "ALPHAPILOT_CRYPTO_GLASSNODE_METRICS"


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


def _metrics(value: str | None) -> tuple[str, ...]:
    if value is None or not str(value).strip():
        return tuple(METRICS)
    return tuple(part.strip().upper() for part in str(value).split(",") if part.strip())


@dataclass(frozen=True)
class CryptoOnchainCaptureRuntimeConfig:
    archive_enabled: bool = False
    database_url: str = ""
    glassnode_enabled: bool = False
    api_key: str = ""
    poll_seconds: int = 600
    interval: str = "1h"
    lookback_hours: int = 4
    metrics: tuple[str, ...] = tuple(METRICS)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CryptoOnchainCaptureRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            archive_enabled=_bool(source.get(ENV_ARCHIVE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            glassnode_enabled=_bool(source.get(ENV_GLASSNODE_ENABLED), False),
            api_key=str(source.get(ENV_GLASSNODE_API_KEY, "") or "").strip(),
            poll_seconds=_int(source.get(ENV_GLASSNODE_POLL_SECONDS), 600),
            interval=str(source.get(ENV_GLASSNODE_INTERVAL, "1h") or "1h").strip(),
            lookback_hours=_int(source.get(ENV_GLASSNODE_LOOKBACK_HOURS), 4),
            metrics=_metrics(source.get(ENV_GLASSNODE_METRICS)),
        ).validated()

    def validated(self) -> "CryptoOnchainCaptureRuntimeConfig":
        CryptoOnchainCapturePolicy(enabled=self.glassnode_enabled, poll_seconds=self.poll_seconds).validated()
        GlassnodeBtcOnchainPolicy(
            enabled=self.glassnode_enabled,
            api_key=self.api_key,
            interval=self.interval,
            lookback_hours=self.lookback_hours,
            metrics=self.metrics,
        ).validated()
        if self.archive_enabled and not self.database_url:
            raise ValueError("crypto PIT archive enabled but DATABASE_URL is missing")
        if self.glassnode_enabled and not self.archive_enabled:
            raise ValueError("Glassnode on-chain capture cannot run without immutable PIT archive enabled")
        if self.glassnode_enabled and not self.database_url:
            raise ValueError("Glassnode on-chain capture cannot run without DATABASE_URL")
        return self


def build_crypto_onchain_capture_runtime(config: CryptoOnchainCaptureRuntimeConfig, *, http_client=None) -> dict:
    config = config.validated()
    if not config.archive_enabled:
        return {
            "status": "CRYPTO_ONCHAIN_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "provider": None,
            "scheduler": None,
        }

    store = PostgresBtcPitArchiveStore(config.database_url)
    provider = GlassnodeBtcOnchainProvider(
        GlassnodeBtcOnchainPolicy(
            enabled=config.glassnode_enabled,
            api_key=config.api_key,
            interval=config.interval,
            lookback_hours=config.lookback_hours,
            metrics=config.metrics,
        ),
        client=http_client,
    )
    scheduler = CryptoOnchainPitCaptureScheduler(
        provider=provider,
        store=store,
        policy=CryptoOnchainCapturePolicy(enabled=config.glassnode_enabled, poll_seconds=config.poll_seconds),
    )
    return {
        "status": "CRYPTO_ONCHAIN_RUNTIME_READY" if config.glassnode_enabled else "CRYPTO_ONCHAIN_ARCHIVE_ONLY_READY",
        "config": config,
        "store": store,
        "provider": provider,
        "scheduler": scheduler,
    }


async def initialize_crypto_onchain_capture_runtime(config: CryptoOnchainCaptureRuntimeConfig, *, http_client=None) -> dict:
    runtime = build_crypto_onchain_capture_runtime(config, http_client=http_client)
    if runtime["store"] is None:
        return {"status": "CRYPTO_ONCHAIN_RUNTIME_DISABLED", "schema_initialized": False, "capture_started": False}
    initialized = await runtime["store"].initialize()
    return {
        "status": runtime["status"],
        "schema_initialized": initialized["status"] == "BTC_PIT_POSTGRES_SCHEMA_READY",
        "capture_started": False,
        "scheduler_enabled": runtime["scheduler"].policy.enabled,
    }


async def run_crypto_onchain_capture_service(config: CryptoOnchainCaptureRuntimeConfig, *, stop_event, http_client=None) -> dict:
    runtime = build_crypto_onchain_capture_runtime(config, http_client=http_client)
    if runtime["scheduler"] is None:
        return {"status": "CRYPTO_ONCHAIN_RUNTIME_DISABLED", "cycles": 0}
    await runtime["store"].initialize()
    return await runtime["scheduler"].run_until_stopped(stop_event)


def runtime_status(config: CryptoOnchainCaptureRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "CRYPTO_ONCHAIN_CAPTURE_RUNTIME_STATUS_V1",
        "archive_enabled": config.archive_enabled,
        "glassnode_enabled": config.glassnode_enabled,
        "database_configured": bool(config.database_url),
        "api_key_configured": bool(config.api_key),
        "poll_seconds": config.poll_seconds,
        "interval": config.interval,
        "metrics": list(config.metrics),
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_ONCHAIN_CAPTURE_RUNTIME_V1",
        "glassnode_enabled_by_default": False,
        "api_key_required_when_enabled": True,
        "archive_required_before_capture": True,
        "database_required_before_capture": True,
        "derivatives_or_news_switch_enables_onchain": False,
        "automatic_startup_registration": False,
        "automatic_network_request": False,
        "trade_generation_enabled": False,
        "research_only": True,
    }
