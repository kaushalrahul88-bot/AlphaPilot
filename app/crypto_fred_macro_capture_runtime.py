"""Environment-gated runtime for prospective FRED BTC macro-regime PIT capture.

Building, status inspection and schema initialization perform no FRED request.
Live collection requires an explicit FRED switch, API key, immutable BTC PIT
archive and DATABASE_URL. This runtime does not enable any other Crypto provider
or any trade-generation/execution path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_fred_macro_capture_scheduler import FredMacroCapturePolicy, FredMacroPitCaptureScheduler
from app.fred_btc_macro_regime_provider import FredBtcMacroRegimeProvider, FredMacroRegimePolicy

ENV_ARCHIVE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_FRED_MACRO_ENABLED = "ALPHAPILOT_CRYPTO_FRED_MACRO_ENABLED"
ENV_FRED_API_KEY = "ALPHAPILOT_CRYPTO_FRED_API_KEY"
ENV_FRED_MACRO_POLL_SECONDS = "ALPHAPILOT_CRYPTO_FRED_MACRO_POLL_SECONDS"
ENV_FRED_MACRO_LOOKBACK_DAYS = "ALPHAPILOT_CRYPTO_FRED_MACRO_LOOKBACK_DAYS"


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
class FredMacroRuntimeConfig:
    archive_enabled: bool = False
    database_url: str = ""
    fred_enabled: bool = False
    api_key: str = ""
    poll_seconds: int = 3600
    lookback_days: int = 45

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "FredMacroRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            archive_enabled=_bool(source.get(ENV_ARCHIVE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            fred_enabled=_bool(source.get(ENV_FRED_MACRO_ENABLED), False),
            api_key=str(source.get(ENV_FRED_API_KEY, "") or "").strip(),
            poll_seconds=_int(source.get(ENV_FRED_MACRO_POLL_SECONDS), 3600),
            lookback_days=_int(source.get(ENV_FRED_MACRO_LOOKBACK_DAYS), 45),
        ).validated()

    def validated(self) -> "FredMacroRuntimeConfig":
        FredMacroCapturePolicy(enabled=self.fred_enabled, poll_seconds=self.poll_seconds).validated()
        FredMacroRegimePolicy(
            enabled=self.fred_enabled,
            api_key=self.api_key,
            lookback_days=self.lookback_days,
        ).validated()
        if self.archive_enabled and not self.database_url:
            raise ValueError("crypto PIT archive enabled but DATABASE_URL is missing")
        if self.fred_enabled and not self.archive_enabled:
            raise ValueError("FRED macro capture cannot run without immutable PIT archive enabled")
        if self.fred_enabled and not self.database_url:
            raise ValueError("FRED macro capture cannot run without DATABASE_URL")
        return self


def build_fred_macro_runtime(
    config: FredMacroRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    config = config.validated()
    if not config.archive_enabled:
        return {
            "status": "FRED_MACRO_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "provider": None,
            "scheduler": None,
        }

    pit_store = store if store is not None else PostgresBtcPitArchiveStore(config.database_url)
    provider = FredBtcMacroRegimeProvider(
        FredMacroRegimePolicy(
            enabled=config.fred_enabled,
            api_key=config.api_key,
            lookback_days=config.lookback_days,
        ),
        client=http_client,
        clock=clock,
    )
    scheduler = FredMacroPitCaptureScheduler(
        provider=provider,
        store=pit_store,
        policy=FredMacroCapturePolicy(enabled=config.fred_enabled, poll_seconds=config.poll_seconds),
    )
    return {
        "status": "FRED_MACRO_RUNTIME_READY" if config.fred_enabled else "FRED_MACRO_ARCHIVE_ONLY_READY",
        "config": config,
        "store": pit_store,
        "provider": provider,
        "scheduler": scheduler,
    }


async def initialize_fred_macro_runtime(
    config: FredMacroRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    runtime = build_fred_macro_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["store"] is None:
        return {
            "status": "FRED_MACRO_RUNTIME_DISABLED",
            "schema_initialized": False,
            "capture_started": False,
            "network_request_performed": False,
        }
    initialized = await runtime["store"].initialize()
    return {
        "status": runtime["status"],
        "schema_initialized": initialized.get("status") == "BTC_PIT_POSTGRES_SCHEMA_READY",
        "capture_started": False,
        "scheduler_enabled": runtime["scheduler"].policy.enabled,
        "network_request_performed": False,
    }


async def run_fred_macro_service(
    config: FredMacroRuntimeConfig,
    *,
    stop_event,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    runtime = build_fred_macro_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["scheduler"] is None or not config.fred_enabled:
        return {"status": "FRED_MACRO_RUNTIME_DISABLED", "cycles": 0, "trade_generated": False}
    await runtime["store"].initialize()
    return await runtime["scheduler"].run_until_stopped(stop_event)


def runtime_status(config: FredMacroRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "FRED_MACRO_RUNTIME_STATUS_V1",
        "archive_enabled": config.archive_enabled,
        "fred_enabled": config.fred_enabled,
        "database_configured": bool(config.database_url),
        "api_key_configured": bool(config.api_key),
        "poll_seconds": config.poll_seconds,
        "lookback_days": config.lookback_days,
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "historical_reconstruction_started": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "FRED_MACRO_CAPTURE_RUNTIME_V1",
        "fred_enabled_by_default": False,
        "separate_environment_switch": ENV_FRED_MACRO_ENABLED,
        "api_key_required_before_capture": True,
        "archive_required_before_capture": True,
        "database_required_before_capture": True,
        "build_performs_network_request": False,
        "status_performs_network_request": False,
        "schema_initialization_starts_capture": False,
        "historical_reconstruction_started_by_live_runtime": False,
        "news_derivatives_onchain_or_stablecoin_switch_enables_fred": False,
        "automatic_startup_registration": False,
        "daily_regime_may_supply_second_intraday_origin": False,
        "options_trade_generation_enabled": False,
        "futures_trade_generation_enabled": False,
        "research_only": True,
    }
