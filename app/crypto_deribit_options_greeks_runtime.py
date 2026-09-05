"""Environment-gated runtime for Deribit BTC ticker-Greeks PIT streaming.

Building or inspecting the runtime performs no HTTP/WebSocket request. The
stream is separately gated from periodic Deribit chain context and from all
CoinDCX execution paths. When explicitly run, the service seeds authoritative
option metadata once via the documented Deribit public instruments endpoint,
then subscribes to documented public ticker channels.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_deribit_options_greeks_stream import (
    DeribitOptionsGreeksStreamPolicy,
    DeribitOptionsGreeksStreamService,
)
from app.deribit_btc_options_context_provider import (
    DeribitBtcOptionsContextPolicy,
    DeribitBtcOptionsContextProvider,
)
from app.deribit_btc_options_ticker_greeks import normalize_option_instruments

ENV_ARCHIVE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_DERIBIT_GREEKS_ENABLED = "ALPHAPILOT_CRYPTO_DERIBIT_OPTIONS_GREEKS_ENABLED"
ENV_DERIBIT_GREEKS_INTERVAL = "ALPHAPILOT_CRYPTO_DERIBIT_OPTIONS_GREEKS_INTERVAL"
ENV_DERIBIT_GREEKS_MAX_EXPIRIES = "ALPHAPILOT_CRYPTO_DERIBIT_OPTIONS_GREEKS_MAX_EXPIRIES"
ENV_DERIBIT_GREEKS_MAX_CHANNELS = "ALPHAPILOT_CRYPTO_DERIBIT_OPTIONS_GREEKS_MAX_CHANNELS"
ENV_DERIBIT_GREEKS_ARCHIVE_SECONDS = "ALPHAPILOT_CRYPTO_DERIBIT_OPTIONS_GREEKS_ARCHIVE_SECONDS"


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
class DeribitOptionsGreeksRuntimeConfig:
    archive_enabled: bool = False
    database_url: str = ""
    greeks_enabled: bool = False
    ticker_interval: str = "agg2"
    max_expiries: int = 2
    max_channels: int = 300
    archive_min_interval_seconds: int = 10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DeribitOptionsGreeksRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            archive_enabled=_bool(source.get(ENV_ARCHIVE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            greeks_enabled=_bool(source.get(ENV_DERIBIT_GREEKS_ENABLED), False),
            ticker_interval=str(source.get(ENV_DERIBIT_GREEKS_INTERVAL, "agg2") or "").strip(),
            max_expiries=_int(source.get(ENV_DERIBIT_GREEKS_MAX_EXPIRIES), 2),
            max_channels=_int(source.get(ENV_DERIBIT_GREEKS_MAX_CHANNELS), 300),
            archive_min_interval_seconds=_int(source.get(ENV_DERIBIT_GREEKS_ARCHIVE_SECONDS), 10),
        ).validated()

    def stream_policy(self) -> DeribitOptionsGreeksStreamPolicy:
        return DeribitOptionsGreeksStreamPolicy(
            enabled=self.greeks_enabled,
            ticker_interval=self.ticker_interval,
            max_expiries=self.max_expiries,
            max_channels=self.max_channels,
            archive_min_interval_seconds=self.archive_min_interval_seconds,
        ).validated()

    def validated(self) -> "DeribitOptionsGreeksRuntimeConfig":
        self.stream_policy()
        if self.archive_enabled and not self.database_url:
            raise ValueError("crypto PIT archive enabled but DATABASE_URL is missing")
        if self.greeks_enabled and not self.archive_enabled:
            raise ValueError("Deribit options Greeks stream cannot run without immutable PIT archive enabled")
        if self.greeks_enabled and not self.database_url:
            raise ValueError("Deribit options Greeks stream cannot run without DATABASE_URL")
        return self


def build_deribit_options_greeks_runtime(
    config: DeribitOptionsGreeksRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    config = config.validated()
    if not config.archive_enabled:
        return {
            "status": "DERIBIT_OPTIONS_GREEKS_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "instrument_provider": None,
            "stream_policy": config.stream_policy(),
            "service": None,
        }

    pit_store = store if store is not None else PostgresBtcPitArchiveStore(config.database_url)
    instrument_provider = DeribitBtcOptionsContextProvider(
        DeribitBtcOptionsContextPolicy(enabled=config.greeks_enabled),
        client=http_client,
        clock=clock,
    )
    return {
        "status": "DERIBIT_OPTIONS_GREEKS_RUNTIME_READY" if config.greeks_enabled else "DERIBIT_OPTIONS_GREEKS_ARCHIVE_ONLY_READY",
        "config": config,
        "store": pit_store,
        "instrument_provider": instrument_provider,
        "stream_policy": config.stream_policy(),
        "service": None,
    }


async def initialize_deribit_options_greeks_runtime(
    config: DeribitOptionsGreeksRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    runtime = build_deribit_options_greeks_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["store"] is None:
        return {
            "status": "DERIBIT_OPTIONS_GREEKS_RUNTIME_DISABLED",
            "schema_initialized": False,
            "instrument_seeded": False,
            "stream_started": False,
            "network_request_performed": False,
        }
    initialized = await runtime["store"].initialize()
    return {
        "status": runtime["status"],
        "schema_initialized": initialized.get("status") == "BTC_PIT_POSTGRES_SCHEMA_READY",
        "instrument_seeded": False,
        "stream_started": False,
        "network_request_performed": False,
    }


async def run_deribit_options_greeks_service(
    config: DeribitOptionsGreeksRuntimeConfig,
    *,
    stop_event: asyncio.Event,
    http_client=None,
    clock=None,
    websocket_connector=None,
    store: Any | None = None,
) -> dict:
    runtime = build_deribit_options_greeks_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["store"] is None or not config.greeks_enabled:
        return {
            "status": "DERIBIT_OPTIONS_GREEKS_RUNTIME_DISABLED",
            "instrument_seeded": False,
            "stream_started": False,
            "trade_generated": False,
        }

    await runtime["store"].initialize()
    clock_fn = clock or (lambda: datetime.now(timezone.utc))
    seed_time = clock_fn()
    raw_rows = await asyncio.to_thread(runtime["instrument_provider"].instrument_rows)
    instruments = normalize_option_instruments(
        raw_rows,
        as_of=seed_time,
        min_seconds_to_expiry=runtime["stream_policy"].min_seconds_to_expiry,
    )
    service = DeribitOptionsGreeksStreamService(
        instruments=instruments,
        store=runtime["store"],
        policy=runtime["stream_policy"],
        clock=clock_fn,
        websocket_connector=websocket_connector,
    )
    result = await service.run_session(stop_event)
    return {
        **result,
        "instrument_seeded": True,
        "seeded_instrument_count": len(instruments),
        "stream_started": result.get("connection_opened") is True,
        "trade_generated": False,
    }


def runtime_status(config: DeribitOptionsGreeksRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "DERIBIT_OPTIONS_GREEKS_RUNTIME_STATUS_V1",
        "archive_enabled": config.archive_enabled,
        "greeks_enabled": config.greeks_enabled,
        "database_configured": bool(config.database_url),
        "ticker_interval": config.ticker_interval,
        "max_expiries": config.max_expiries,
        "max_channels": config.max_channels,
        "archive_min_interval_seconds": config.archive_min_interval_seconds,
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "instrument_seeded": False,
        "websocket_opened": False,
        "deribit_context_switch_required": False,
        "coindcx_switch_required": False,
        "coindcx_contract_selection_enabled": False,
        "coindcx_quote_fill_enabled": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "DERIBIT_OPTIONS_GREEKS_RUNTIME_V1",
        "enabled_by_default": False,
        "separate_environment_switch": ENV_DERIBIT_GREEKS_ENABLED,
        "archive_required_before_stream": True,
        "database_required_before_stream": True,
        "schema_initialization_starts_stream": False,
        "status_check_performs_network_request": False,
        "build_performs_network_request": False,
        "instrument_seed_occurs_only_when_service_runs": True,
        "instrument_seed_uses_documented_deribit_endpoint": True,
        "websocket_uses_documented_public_ticker": True,
        "automatic_startup_registration": False,
        "periodic_deribit_context_switch_enables_greeks_stream": False,
        "coindcx_options_switch_enables_greeks_stream": False,
        "greeks_stream_enables_coindcx_execution": False,
        "coindcx_contract_selection_enabled": False,
        "coindcx_quote_fill_enabled": False,
        "options_trade_generation_enabled": False,
        "futures_trade_generation_enabled": False,
        "research_only": True,
    }
