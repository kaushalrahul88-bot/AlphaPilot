"""Environment-gated runtime for Deribit BTC global options PIT context capture."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_deribit_options_capture_scheduler import DeribitOptionsCapturePolicy, DeribitOptionsPitCaptureScheduler
from app.deribit_btc_options_context_provider import DeribitBtcOptionsContextPolicy, DeribitBtcOptionsContextProvider

ENV_ARCHIVE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_DERIBIT_OPTIONS_ENABLED = "ALPHAPILOT_CRYPTO_DERIBIT_OPTIONS_CONTEXT_ENABLED"
ENV_DERIBIT_OPTIONS_POLL_SECONDS = "ALPHAPILOT_CRYPTO_DERIBIT_OPTIONS_CONTEXT_POLL_SECONDS"


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
class DeribitOptionsRuntimeConfig:
    archive_enabled: bool = False
    database_url: str = ""
    deribit_enabled: bool = False
    poll_seconds: int = 300

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DeribitOptionsRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            archive_enabled=_bool(source.get(ENV_ARCHIVE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            deribit_enabled=_bool(source.get(ENV_DERIBIT_OPTIONS_ENABLED), False),
            poll_seconds=_int(source.get(ENV_DERIBIT_OPTIONS_POLL_SECONDS), 300),
        ).validated()

    def validated(self) -> "DeribitOptionsRuntimeConfig":
        DeribitOptionsCapturePolicy(enabled=self.deribit_enabled, poll_seconds=self.poll_seconds).validated()
        if self.archive_enabled and not self.database_url:
            raise ValueError("crypto PIT archive enabled but DATABASE_URL is missing")
        if self.deribit_enabled and not self.archive_enabled:
            raise ValueError("Deribit options context capture cannot run without immutable PIT archive enabled")
        if self.deribit_enabled and not self.database_url:
            raise ValueError("Deribit options context capture cannot run without DATABASE_URL")
        return self


def build_deribit_options_runtime(config: DeribitOptionsRuntimeConfig, *, http_client=None, clock=None) -> dict:
    config = config.validated()
    if not config.archive_enabled:
        return {
            "status": "DERIBIT_OPTIONS_CONTEXT_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "provider": None,
            "scheduler": None,
        }

    store = PostgresBtcPitArchiveStore(config.database_url)
    provider = DeribitBtcOptionsContextProvider(
        DeribitBtcOptionsContextPolicy(enabled=config.deribit_enabled),
        client=http_client,
        clock=clock,
    )
    scheduler = DeribitOptionsPitCaptureScheduler(
        provider=provider,
        store=store,
        policy=DeribitOptionsCapturePolicy(enabled=config.deribit_enabled, poll_seconds=config.poll_seconds),
    )
    return {
        "status": "DERIBIT_OPTIONS_CONTEXT_RUNTIME_READY" if config.deribit_enabled else "DERIBIT_OPTIONS_CONTEXT_ARCHIVE_ONLY_READY",
        "config": config,
        "store": store,
        "provider": provider,
        "scheduler": scheduler,
    }


async def initialize_deribit_options_runtime(config: DeribitOptionsRuntimeConfig, *, http_client=None, clock=None) -> dict:
    runtime = build_deribit_options_runtime(config, http_client=http_client, clock=clock)
    if runtime["store"] is None:
        return {"status": "DERIBIT_OPTIONS_CONTEXT_RUNTIME_DISABLED", "schema_initialized": False, "capture_started": False}
    initialized = await runtime["store"].initialize()
    return {
        "status": runtime["status"],
        "schema_initialized": initialized["status"] == "BTC_PIT_POSTGRES_SCHEMA_READY",
        "capture_started": False,
        "scheduler_enabled": runtime["scheduler"].policy.enabled,
    }


async def run_deribit_options_service(config: DeribitOptionsRuntimeConfig, *, stop_event, http_client=None, clock=None) -> dict:
    runtime = build_deribit_options_runtime(config, http_client=http_client, clock=clock)
    if runtime["scheduler"] is None:
        return {"status": "DERIBIT_OPTIONS_CONTEXT_RUNTIME_DISABLED", "cycles": 0}
    await runtime["store"].initialize()
    return await runtime["scheduler"].run_until_stopped(stop_event)


def runtime_status(config: DeribitOptionsRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "DERIBIT_OPTIONS_CONTEXT_RUNTIME_STATUS_V1",
        "archive_enabled": config.archive_enabled,
        "deribit_enabled": config.deribit_enabled,
        "database_configured": bool(config.database_url),
        "poll_seconds": config.poll_seconds,
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "instrument_metadata_refresh_automatic": False,
        "global_options_context_only": True,
        "coindcx_contract_selection_enabled": False,
        "coindcx_quote_fill_enabled": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "DERIBIT_OPTIONS_CONTEXT_RUNTIME_V1",
        "enabled_by_default": False,
        "api_key_required": False,
        "archive_required_before_capture": True,
        "database_required_before_capture": True,
        "automatic_startup_registration": False,
        "automatic_network_request": False,
        "instrument_metadata_refresh_automatic": False,
        "coindcx_options_capture_switch_enables_deribit": False,
        "deribit_switch_enables_coindcx_execution": False,
        "global_options_context_only": True,
        "coindcx_contract_selection_enabled": False,
        "coindcx_quote_fill_enabled": False,
        "trade_generation_enabled": False,
        "research_only": True,
    }
