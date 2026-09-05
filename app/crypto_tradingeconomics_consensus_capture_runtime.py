"""Environment-gated runtime for Trading Economics pre-release consensus capture.

The runtime is independent from BLS official releases and every other Crypto
Brain provider. Build/status/schema initialization are network-free. Live
capture requires the immutable BTC PIT archive, DATABASE_URL, a dedicated
Trading Economics switch, API key, and explicit event targets with official
release timestamps.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_tradingeconomics_consensus_capture_scheduler import (
    TradingEconomicsConsensusCapturePolicy,
    TradingEconomicsConsensusPitCaptureScheduler,
)
from app.tradingeconomics_macro_consensus_provider import (
    TradingEconomicsConsensusPolicy,
    TradingEconomicsConsensusTarget,
    TradingEconomicsMacroConsensusProvider,
)

ENV_ARCHIVE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_TE_ENABLED = "ALPHAPILOT_CRYPTO_TRADING_ECONOMICS_CONSENSUS_ENABLED"
ENV_TE_API_KEY = "TRADING_ECONOMICS_API_KEY"
ENV_TE_TARGETS_JSON = "ALPHAPILOT_CRYPTO_TRADING_ECONOMICS_CONSENSUS_TARGETS_JSON"
ENV_TE_POLL_SECONDS = "ALPHAPILOT_CRYPTO_TRADING_ECONOMICS_CONSENSUS_POLL_SECONDS"


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


def _aware_datetime(value: object, *, name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


def _targets(value: str | None) -> tuple[TradingEconomicsConsensusTarget, ...]:
    if value is None or not str(value).strip():
        return ()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Trading Economics consensus targets environment value must be valid JSON") from exc
    if not isinstance(decoded, list):
        raise ValueError("Trading Economics consensus targets JSON must be a list")
    expected_keys = {"event_key", "event_type", "reference_period", "expected_release_at"}
    rows: list[TradingEconomicsConsensusTarget] = []
    for index, raw in enumerate(decoded):
        if not isinstance(raw, dict):
            raise ValueError(f"Trading Economics consensus target at index {index} must be an object")
        if set(raw) != expected_keys:
            raise ValueError(
                f"Trading Economics consensus target at index {index} must contain exactly {sorted(expected_keys)}"
            )
        rows.append(TradingEconomicsConsensusTarget(
            event_key=str(raw["event_key"]),
            event_type=str(raw["event_type"]),
            reference_period=str(raw["reference_period"]),
            expected_release_at=_aware_datetime(raw["expected_release_at"], name="expected_release_at"),
        ).validated())
    identities = [row.event_key for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate Trading Economics consensus target event_key in runtime configuration")
    return tuple(rows)


@dataclass(frozen=True)
class TradingEconomicsConsensusRuntimeConfig:
    archive_enabled: bool = False
    database_url: str = ""
    consensus_enabled: bool = False
    api_key: str = ""
    targets: tuple[TradingEconomicsConsensusTarget, ...] = ()
    poll_seconds: int = 300

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TradingEconomicsConsensusRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            archive_enabled=_bool(source.get(ENV_ARCHIVE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            consensus_enabled=_bool(source.get(ENV_TE_ENABLED), False),
            api_key=str(source.get(ENV_TE_API_KEY, "") or "").strip(),
            targets=_targets(source.get(ENV_TE_TARGETS_JSON)),
            poll_seconds=_int(source.get(ENV_TE_POLL_SECONDS), 300),
        ).validated()

    def validated(self) -> "TradingEconomicsConsensusRuntimeConfig":
        TradingEconomicsConsensusCapturePolicy(
            enabled=self.consensus_enabled,
            poll_seconds=self.poll_seconds,
        ).validated()
        TradingEconomicsConsensusPolicy(
            enabled=self.consensus_enabled,
            api_key=self.api_key,
        ).validated()
        for target in self.targets:
            target.validated()
        if self.archive_enabled and not self.database_url:
            raise ValueError("crypto PIT archive enabled but DATABASE_URL is missing")
        if self.consensus_enabled and not self.archive_enabled:
            raise ValueError("Trading Economics consensus capture cannot run without immutable PIT archive enabled")
        if self.consensus_enabled and not self.database_url:
            raise ValueError("Trading Economics consensus capture cannot run without DATABASE_URL")
        if self.consensus_enabled and not self.targets:
            raise ValueError("Trading Economics consensus capture enabled but no explicit targets are configured")
        return self


def build_tradingeconomics_consensus_runtime(
    config: TradingEconomicsConsensusRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    config = config.validated()
    if not config.archive_enabled:
        return {
            "status": "TRADING_ECONOMICS_CONSENSUS_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "provider": None,
            "scheduler": None,
        }

    pit_store = store if store is not None else PostgresBtcPitArchiveStore(config.database_url)
    provider = TradingEconomicsMacroConsensusProvider(
        TradingEconomicsConsensusPolicy(enabled=config.consensus_enabled, api_key=config.api_key),
        client=http_client,
        clock=clock,
    )
    scheduler = TradingEconomicsConsensusPitCaptureScheduler(
        provider=provider,
        store=pit_store,
        targets=config.targets,
        policy=TradingEconomicsConsensusCapturePolicy(
            enabled=config.consensus_enabled,
            poll_seconds=config.poll_seconds,
        ),
    )
    return {
        "status": (
            "TRADING_ECONOMICS_CONSENSUS_RUNTIME_READY"
            if config.consensus_enabled else "TRADING_ECONOMICS_CONSENSUS_ARCHIVE_ONLY_READY"
        ),
        "config": config,
        "store": pit_store,
        "provider": provider,
        "scheduler": scheduler,
    }


async def initialize_tradingeconomics_consensus_runtime(
    config: TradingEconomicsConsensusRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    runtime = build_tradingeconomics_consensus_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["store"] is None:
        return {
            "status": "TRADING_ECONOMICS_CONSENSUS_RUNTIME_DISABLED",
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
        "target_count": len(config.targets),
        "network_request_performed": False,
    }


async def run_tradingeconomics_consensus_service(
    config: TradingEconomicsConsensusRuntimeConfig,
    *,
    stop_event,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    runtime = build_tradingeconomics_consensus_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["scheduler"] is None or not config.consensus_enabled:
        return {"status": "TRADING_ECONOMICS_CONSENSUS_RUNTIME_DISABLED", "cycles": 0, "trade_generated": False}
    await runtime["store"].initialize()
    return await runtime["scheduler"].run_until_stopped(stop_event)


def runtime_status(config: TradingEconomicsConsensusRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "TRADING_ECONOMICS_CONSENSUS_RUNTIME_STATUS_V1",
        "archive_enabled": config.archive_enabled,
        "consensus_enabled": config.consensus_enabled,
        "database_configured": bool(config.database_url),
        "api_key_configured": bool(config.api_key),
        "target_count": len(config.targets),
        "poll_seconds": config.poll_seconds,
        "automatic_target_discovery": False,
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "bls_release_capture_enabled": False,
        "fred_macro_capture_enabled": False,
        "numeric_surprise_enabled": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "TRADING_ECONOMICS_CONSENSUS_CAPTURE_RUNTIME_V1",
        "enabled_by_default": False,
        "separate_environment_switch": ENV_TE_ENABLED,
        "api_key_environment": ENV_TE_API_KEY,
        "explicit_target_json_required": True,
        "official_release_timestamp_required_in_target": True,
        "automatic_target_discovery": False,
        "archive_required_before_capture": True,
        "database_required_before_capture": True,
        "api_key_required_before_capture": True,
        "build_performs_network_request": False,
        "status_performs_network_request": False,
        "schema_initialization_starts_capture": False,
        "automatic_startup_registration": False,
        "bls_runtime_enables_consensus": False,
        "fred_runtime_enables_consensus": False,
        "news_or_derivatives_runtime_enables_consensus": False,
        "numeric_surprise_enabled_by_runtime": False,
        "options_trade_generation_enabled": False,
        "futures_trade_generation_enabled": False,
        "research_only": True,
    }
