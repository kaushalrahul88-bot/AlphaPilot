"""Environment-gated runtime for prospective official BLS macro release capture.

The runtime never discovers or guesses a release target. Operators provide an
explicit JSON target list with URL, event_type and expected_event_key. Build,
status and schema initialization are network-free. Live capture requires the BTC
immutable PIT archive, DATABASE_URL and the dedicated BLS switch.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

from app.bls_exact_macro_release_provider import BlsExactMacroReleasePolicy, BlsExactMacroReleaseProvider
from app.crypto_bls_macro_release_capture_scheduler import (
    BlsExactReleasePitCaptureScheduler,
    BlsReleaseCapturePolicy,
    BlsReleaseCaptureTarget,
)
from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore

ENV_ARCHIVE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_BLS_ENABLED = "ALPHAPILOT_CRYPTO_BLS_RELEASES_ENABLED"
ENV_BLS_TARGETS_JSON = "ALPHAPILOT_CRYPTO_BLS_RELEASE_TARGETS_JSON"
ENV_BLS_POLL_SECONDS = "ALPHAPILOT_CRYPTO_BLS_RELEASE_POLL_SECONDS"


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


def _targets(value: str | None) -> tuple[BlsReleaseCaptureTarget, ...]:
    if value is None or not str(value).strip():
        return ()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("BLS release targets environment value must be valid JSON") from exc
    if not isinstance(decoded, list):
        raise ValueError("BLS release targets JSON must be a list")
    rows: list[BlsReleaseCaptureTarget] = []
    for index, raw in enumerate(decoded):
        if not isinstance(raw, dict):
            raise ValueError(f"BLS release target at index {index} must be an object")
        expected_keys = {"url", "event_type", "expected_event_key"}
        if set(raw) != expected_keys:
            raise ValueError(f"BLS release target at index {index} must contain exactly {sorted(expected_keys)}")
        rows.append(BlsReleaseCaptureTarget(
            url=str(raw["url"]),
            event_type=str(raw["event_type"]),
            expected_event_key=str(raw["expected_event_key"]),
        ).validated())
    identities = [(row.event_type, row.expected_event_key) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate BLS exact-release target in runtime configuration")
    return tuple(rows)


@dataclass(frozen=True)
class BlsReleaseRuntimeConfig:
    archive_enabled: bool = False
    database_url: str = ""
    bls_enabled: bool = False
    targets: tuple[BlsReleaseCaptureTarget, ...] = ()
    poll_seconds: int = 30

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BlsReleaseRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            archive_enabled=_bool(source.get(ENV_ARCHIVE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            bls_enabled=_bool(source.get(ENV_BLS_ENABLED), False),
            targets=_targets(source.get(ENV_BLS_TARGETS_JSON)),
            poll_seconds=_int(source.get(ENV_BLS_POLL_SECONDS), 30),
        ).validated()

    def validated(self) -> "BlsReleaseRuntimeConfig":
        BlsReleaseCapturePolicy(enabled=self.bls_enabled, poll_seconds=self.poll_seconds).validated()
        for target in self.targets:
            target.validated()
        if self.archive_enabled and not self.database_url:
            raise ValueError("crypto PIT archive enabled but DATABASE_URL is missing")
        if self.bls_enabled and not self.archive_enabled:
            raise ValueError("BLS release capture cannot run without immutable PIT archive enabled")
        if self.bls_enabled and not self.database_url:
            raise ValueError("BLS release capture cannot run without DATABASE_URL")
        if self.bls_enabled and not self.targets:
            raise ValueError("BLS release capture enabled but no explicit targets are configured")
        return self


def build_bls_release_runtime(
    config: BlsReleaseRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    config = config.validated()
    if not config.archive_enabled:
        return {
            "status": "BLS_RELEASE_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "provider": None,
            "scheduler": None,
        }

    pit_store = store if store is not None else PostgresBtcPitArchiveStore(config.database_url)
    provider = BlsExactMacroReleaseProvider(
        BlsExactMacroReleasePolicy(enabled=config.bls_enabled),
        client=http_client,
        clock=clock,
    )
    scheduler = BlsExactReleasePitCaptureScheduler(
        provider=provider,
        store=pit_store,
        targets=config.targets,
        policy=BlsReleaseCapturePolicy(enabled=config.bls_enabled, poll_seconds=config.poll_seconds),
    )
    return {
        "status": "BLS_RELEASE_RUNTIME_READY" if config.bls_enabled else "BLS_RELEASE_ARCHIVE_ONLY_READY",
        "config": config,
        "store": pit_store,
        "provider": provider,
        "scheduler": scheduler,
    }


async def initialize_bls_release_runtime(
    config: BlsReleaseRuntimeConfig,
    *,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    runtime = build_bls_release_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["store"] is None:
        return {
            "status": "BLS_RELEASE_RUNTIME_DISABLED",
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


async def run_bls_release_service(
    config: BlsReleaseRuntimeConfig,
    *,
    stop_event,
    http_client=None,
    clock=None,
    store: Any | None = None,
) -> dict:
    runtime = build_bls_release_runtime(config, http_client=http_client, clock=clock, store=store)
    if runtime["scheduler"] is None or not config.bls_enabled:
        return {"status": "BLS_RELEASE_RUNTIME_DISABLED", "cycles": 0, "trade_generated": False}
    await runtime["store"].initialize()
    return await runtime["scheduler"].run_until_stopped(stop_event)


def runtime_status(config: BlsReleaseRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "BLS_EXACT_RELEASE_RUNTIME_STATUS_V1",
        "archive_enabled": config.archive_enabled,
        "bls_enabled": config.bls_enabled,
        "database_configured": bool(config.database_url),
        "target_count": len(config.targets),
        "poll_seconds": config.poll_seconds,
        "automatic_target_discovery": False,
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "consensus_capture_enabled": False,
        "surprise_direction_enabled": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BLS_EXACT_RELEASE_CAPTURE_RUNTIME_V1",
        "enabled_by_default": False,
        "separate_environment_switch": ENV_BLS_ENABLED,
        "explicit_target_json_required": True,
        "automatic_target_discovery": False,
        "archive_required_before_capture": True,
        "database_required_before_capture": True,
        "build_performs_network_request": False,
        "status_performs_network_request": False,
        "schema_initialization_starts_capture": False,
        "automatic_startup_registration": False,
        "consensus_provider_enabled_by_bls_runtime": False,
        "surprise_direction_enabled_by_bls_runtime": False,
        "options_trade_generation_enabled": False,
        "futures_trade_generation_enabled": False,
        "research_only": True,
    }
