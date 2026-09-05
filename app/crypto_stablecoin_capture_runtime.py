"""Environment-gated runtime for aggregate stablecoin supply PIT capture."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_stablecoin_capture_scheduler import StablecoinSupplyCapturePolicy, StablecoinSupplyPitCaptureScheduler
from app.defillama_stablecoin_provider import DefiLlamaStablecoinPolicy, DefiLlamaStablecoinProvider

ENV_ARCHIVE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_STABLECOIN_ENABLED = "ALPHAPILOT_CRYPTO_DEFILLAMA_STABLECOIN_ENABLED"
ENV_STABLECOIN_POLL_SECONDS = "ALPHAPILOT_CRYPTO_DEFILLAMA_STABLECOIN_POLL_SECONDS"
ENV_STABLECOIN_PEG_TYPE = "ALPHAPILOT_CRYPTO_DEFILLAMA_STABLECOIN_PEG_TYPE"


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


def _explicit_text(source: Mapping[str, str], key: str, default: str) -> str:
    if key not in source:
        return default
    return str(source.get(key, "") or "").strip()


@dataclass(frozen=True)
class StablecoinSupplyRuntimeConfig:
    archive_enabled: bool = False
    database_url: str = ""
    stablecoin_enabled: bool = False
    poll_seconds: int = 3600
    peg_type: str = "peggedUSD"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "StablecoinSupplyRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            archive_enabled=_bool(source.get(ENV_ARCHIVE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            stablecoin_enabled=_bool(source.get(ENV_STABLECOIN_ENABLED), False),
            poll_seconds=_int(source.get(ENV_STABLECOIN_POLL_SECONDS), 3600),
            peg_type=_explicit_text(source, ENV_STABLECOIN_PEG_TYPE, "peggedUSD"),
        ).validated()

    def validated(self) -> "StablecoinSupplyRuntimeConfig":
        StablecoinSupplyCapturePolicy(enabled=self.stablecoin_enabled, poll_seconds=self.poll_seconds).validated()
        DefiLlamaStablecoinPolicy(enabled=self.stablecoin_enabled, peg_type=self.peg_type).validated()
        if self.archive_enabled and not self.database_url:
            raise ValueError("crypto PIT archive enabled but DATABASE_URL is missing")
        if self.stablecoin_enabled and not self.archive_enabled:
            raise ValueError("stablecoin supply capture cannot run without immutable PIT archive enabled")
        if self.stablecoin_enabled and not self.database_url:
            raise ValueError("stablecoin supply capture cannot run without DATABASE_URL")
        return self


def build_stablecoin_supply_runtime(config: StablecoinSupplyRuntimeConfig, *, http_client=None) -> dict:
    config = config.validated()
    if not config.archive_enabled:
        return {
            "status": "STABLECOIN_SUPPLY_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "provider": None,
            "scheduler": None,
        }

    store = PostgresBtcPitArchiveStore(config.database_url)
    provider = DefiLlamaStablecoinProvider(
        DefiLlamaStablecoinPolicy(enabled=config.stablecoin_enabled, peg_type=config.peg_type),
        client=http_client,
    )
    scheduler = StablecoinSupplyPitCaptureScheduler(
        provider=provider,
        store=store,
        policy=StablecoinSupplyCapturePolicy(enabled=config.stablecoin_enabled, poll_seconds=config.poll_seconds),
    )
    return {
        "status": "STABLECOIN_SUPPLY_RUNTIME_READY" if config.stablecoin_enabled else "STABLECOIN_SUPPLY_ARCHIVE_ONLY_READY",
        "config": config,
        "store": store,
        "provider": provider,
        "scheduler": scheduler,
    }


async def initialize_stablecoin_supply_runtime(config: StablecoinSupplyRuntimeConfig, *, http_client=None) -> dict:
    runtime = build_stablecoin_supply_runtime(config, http_client=http_client)
    if runtime["store"] is None:
        return {"status": "STABLECOIN_SUPPLY_RUNTIME_DISABLED", "schema_initialized": False, "capture_started": False}
    initialized = await runtime["store"].initialize()
    return {
        "status": runtime["status"],
        "schema_initialized": initialized["status"] == "BTC_PIT_POSTGRES_SCHEMA_READY",
        "capture_started": False,
        "scheduler_enabled": runtime["scheduler"].policy.enabled,
    }


async def run_stablecoin_supply_service(config: StablecoinSupplyRuntimeConfig, *, stop_event, http_client=None) -> dict:
    runtime = build_stablecoin_supply_runtime(config, http_client=http_client)
    if runtime["scheduler"] is None:
        return {"status": "STABLECOIN_SUPPLY_RUNTIME_DISABLED", "cycles": 0}
    await runtime["store"].initialize()
    return await runtime["scheduler"].run_until_stopped(stop_event)


def runtime_status(config: StablecoinSupplyRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "STABLECOIN_SUPPLY_RUNTIME_STATUS_V1",
        "archive_enabled": config.archive_enabled,
        "stablecoin_enabled": config.stablecoin_enabled,
        "database_configured": bool(config.database_url),
        "poll_seconds": config.poll_seconds,
        "peg_type": config.peg_type,
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "STABLECOIN_SUPPLY_RUNTIME_V1",
        "stablecoin_enabled_by_default": False,
        "api_key_required": False,
        "archive_required_before_capture": True,
        "database_required_before_capture": True,
        "derivatives_news_or_onchain_switch_enables_stablecoin": False,
        "automatic_startup_registration": False,
        "automatic_network_request": False,
        "aggregate_supply_equals_exchange_flow": False,
        "trade_generation_enabled": False,
        "research_only": True,
    }
