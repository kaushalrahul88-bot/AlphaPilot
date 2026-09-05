"""Environment-gated runtime for prospective Massive/CME availability audits.

This subsystem proves feed timeliness; it does not provide market direction.
Postgres persistence and the networked audit each have explicit switches. Build,
status and schema initialization are network-free, and no existing BLS/FRED/TE,
News, derivatives, or BTC PIT switch can activate this runtime.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from app.crypto_macro_live_availability_postgres import PostgresMacroLiveAvailabilityStore
from app.crypto_macro_live_availability_scheduler import (
    MacroLiveAvailabilityAuditScheduler,
    MacroLiveAvailabilityCapturePolicy,
    MacroLiveAvailabilityTarget,
)
from app.massive_macro_futures_reaction_provider import (
    MassiveMacroFuturesReactionPolicy,
    MassiveMacroFuturesReactionProvider,
)

ENV_STORE_ENABLED = "ALPHAPILOT_CRYPTO_MACRO_LIVE_AVAILABILITY_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_AUDIT_ENABLED = "ALPHAPILOT_CRYPTO_MACRO_LIVE_AVAILABILITY_AUDIT_ENABLED"
ENV_MASSIVE_API_KEY = "MASSIVE_API_KEY"
ENV_TARGETS_JSON = "ALPHAPILOT_CRYPTO_MACRO_LIVE_AVAILABILITY_TARGETS_JSON"
ENV_POLL_SECONDS = "ALPHAPILOT_CRYPTO_MACRO_LIVE_AVAILABILITY_POLL_SECONDS"
ENV_MAX_LATENCY_SECONDS = "ALPHAPILOT_CRYPTO_MACRO_LIVE_AVAILABILITY_MAX_LATENCY_SECONDS"
ENV_MIN_UNIQUE_EVENTS = "ALPHAPILOT_CRYPTO_MACRO_LIVE_AVAILABILITY_MIN_EVENTS"


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


def _float(value: str | None, default: float) -> float:
    if value is None or not str(value).strip():
        return float(default)
    try:
        return float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"invalid float environment value: {value!r}") from exc


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


def _targets(value: str | None) -> tuple[MacroLiveAvailabilityTarget, ...]:
    if value is None or not str(value).strip():
        return ()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("macro live-availability targets environment value must be valid JSON") from exc
    if not isinstance(decoded, list):
        raise ValueError("macro live-availability targets JSON must be a list")
    expected = {"event_key", "event_type", "release_at"}
    targets: list[MacroLiveAvailabilityTarget] = []
    for index, raw in enumerate(decoded):
        if not isinstance(raw, dict):
            raise ValueError(f"macro live-availability target at index {index} must be an object")
        if set(raw) != expected:
            raise ValueError(
                f"macro live-availability target at index {index} must contain exactly {sorted(expected)}"
            )
        targets.append(MacroLiveAvailabilityTarget(
            event_key=str(raw["event_key"]),
            event_type=str(raw["event_type"]),
            release_at=_aware_datetime(raw["release_at"], name="release_at"),
        ).validated())
    keys = [target.event_key for target in targets]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate macro live-availability target event_key in runtime configuration")
    return tuple(targets)


@dataclass(frozen=True)
class MacroLiveAvailabilityRuntimeConfig:
    store_enabled: bool = False
    database_url: str = ""
    audit_enabled: bool = False
    massive_api_key: str = ""
    targets: tuple[MacroLiveAvailabilityTarget, ...] = ()
    poll_seconds: int = 15
    max_latency_seconds: float = 120.0
    min_unique_events: int = 3

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MacroLiveAvailabilityRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            store_enabled=_bool(source.get(ENV_STORE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            audit_enabled=_bool(source.get(ENV_AUDIT_ENABLED), False),
            massive_api_key=str(source.get(ENV_MASSIVE_API_KEY, "") or "").strip(),
            targets=_targets(source.get(ENV_TARGETS_JSON)),
            poll_seconds=_int(source.get(ENV_POLL_SECONDS), 15),
            max_latency_seconds=_float(source.get(ENV_MAX_LATENCY_SECONDS), 120.0),
            min_unique_events=_int(source.get(ENV_MIN_UNIQUE_EVENTS), 3),
        ).validated()

    def validated(self) -> "MacroLiveAvailabilityRuntimeConfig":
        MacroLiveAvailabilityCapturePolicy(
            enabled=self.audit_enabled,
            poll_seconds=self.poll_seconds,
            max_latency_seconds=self.max_latency_seconds,
            min_unique_events=self.min_unique_events,
        ).validated()
        MassiveMacroFuturesReactionPolicy(
            enabled=self.audit_enabled,
            api_key=self.massive_api_key,
            selection_window_minutes=30,
            reaction_window_minutes=10,
        ).validated()
        for target in self.targets:
            target.validated()
        if self.store_enabled and not self.database_url:
            raise ValueError("macro live-availability Postgres enabled but DATABASE_URL is missing")
        if self.audit_enabled and not self.store_enabled:
            raise ValueError("macro live-availability audit cannot run without its Postgres store enabled")
        if self.audit_enabled and not self.database_url:
            raise ValueError("macro live-availability audit cannot run without DATABASE_URL")
        if self.audit_enabled and not self.targets:
            raise ValueError("macro live-availability audit enabled but no explicit targets are configured")
        return self


def build_macro_live_availability_runtime(
    config: MacroLiveAvailabilityRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    config = config.validated()
    if not config.store_enabled:
        return {
            "status": "MACRO_LIVE_AVAILABILITY_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "provider": None,
            "scheduler": None,
        }

    audit_store = store if store is not None else PostgresMacroLiveAvailabilityStore(config.database_url)
    provider = MassiveMacroFuturesReactionProvider(
        MassiveMacroFuturesReactionPolicy(
            enabled=config.audit_enabled,
            api_key=config.massive_api_key,
            selection_window_minutes=30,
            reaction_window_minutes=10,
        ),
        client=http_client,
        clock=clock,
    )
    scheduler = MacroLiveAvailabilityAuditScheduler(
        provider=provider,
        store=audit_store,
        targets=config.targets,
        policy=MacroLiveAvailabilityCapturePolicy(
            enabled=config.audit_enabled,
            poll_seconds=config.poll_seconds,
            max_latency_seconds=config.max_latency_seconds,
            min_unique_events=config.min_unique_events,
        ),
        clock=clock,
    )
    return {
        "status": (
            "MACRO_LIVE_AVAILABILITY_RUNTIME_READY"
            if config.audit_enabled else "MACRO_LIVE_AVAILABILITY_STORE_ONLY_READY"
        ),
        "config": config,
        "store": audit_store,
        "provider": provider,
        "scheduler": scheduler,
    }


async def initialize_macro_live_availability_runtime(
    config: MacroLiveAvailabilityRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    runtime = build_macro_live_availability_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["store"] is None:
        return {
            "status": "MACRO_LIVE_AVAILABILITY_RUNTIME_DISABLED",
            "schema_initialized": False,
            "audit_started": False,
            "network_request_performed": False,
            "live_confirmation_enabled": False,
        }
    initialized = await runtime["store"].initialize()
    return {
        "status": runtime["status"],
        "schema_initialized": initialized.get("status") == "MACRO_LIVE_AVAILABILITY_POSTGRES_SCHEMA_READY",
        "audit_started": False,
        "scheduler_enabled": runtime["scheduler"].policy.enabled,
        "target_count": len(config.targets),
        "network_request_performed": False,
        "live_confirmation_enabled": False,
    }


async def run_macro_live_availability_service(
    config: MacroLiveAvailabilityRuntimeConfig,
    *,
    stop_event,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    runtime = build_macro_live_availability_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["scheduler"] is None or not config.audit_enabled:
        return {
            "status": "MACRO_LIVE_AVAILABILITY_RUNTIME_DISABLED",
            "cycles": 0,
            "live_confirmation_enabled": False,
            "trade_generated": False,
        }
    await runtime["store"].initialize()
    return await runtime["scheduler"].run_until_stopped(stop_event)


def runtime_status(config: MacroLiveAvailabilityRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "MASSIVE_MACRO_LIVE_AVAILABILITY_RUNTIME_STATUS_V1",
        "store_enabled": config.store_enabled,
        "audit_enabled": config.audit_enabled,
        "database_configured": bool(config.database_url),
        "api_key_configured": bool(config.massive_api_key),
        "target_count": len(config.targets),
        "poll_seconds": config.poll_seconds,
        "max_latency_seconds": config.max_latency_seconds,
        "min_unique_events": config.min_unique_events,
        "provider_plan_label_trusted_as_realtime": False,
        "historical_reconstruction_trusted_as_live_proof": False,
        "automatic_target_discovery": False,
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "live_confirmation_enabled": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "MASSIVE_MACRO_LIVE_AVAILABILITY_RUNTIME_V1",
        "enabled_by_default": False,
        "separate_store_switch": ENV_STORE_ENABLED,
        "separate_audit_switch": ENV_AUDIT_ENABLED,
        "api_key_environment": ENV_MASSIVE_API_KEY,
        "explicit_target_json_required": True,
        "automatic_target_discovery": False,
        "database_required_before_audit": True,
        "store_required_before_audit": True,
        "api_key_required_before_audit": True,
        "build_performs_network_request": False,
        "status_performs_network_request": False,
        "schema_initialization_starts_audit": False,
        "automatic_startup_registration": False,
        "provider_plan_label_trusted_as_realtime": False,
        "historical_reconstruction_trusted_as_live_proof": False,
        "bls_runtime_enables_audit": False,
        "fred_runtime_enables_audit": False,
        "tradingeconomics_runtime_enables_audit": False,
        "news_or_derivatives_runtime_enables_audit": False,
        "qualification_auto_enables_live_confirmation": False,
        "live_confirmation_enabled": False,
        "direction_generation_enabled": False,
        "options_trade_generation_enabled": False,
        "futures_trade_generation_enabled": False,
        "research_only": True,
    }
