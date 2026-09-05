"""Environment-gated runtime for resolved BTC Experience Memory persistence.

Outcome memory is deliberately separate from market-data PIT capture. This module
never auto-registers startup work and never converts an unresolved case into a
resolved one. A caller must explicitly provide a resolved experience entry and
its causal ``resolved_at`` timestamp.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from app.crypto_btc_experience_postgres import PostgresBtcExperienceStore
from app.crypto_btc_experience_store import resolved_experience_record_from_entry

ENV_DATABASE_URL = "DATABASE_URL"
ENV_EXPERIENCE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_EXPERIENCE_POSTGRES_ENABLED"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


@dataclass(frozen=True)
class BtcExperienceRuntimeConfig:
    enabled: bool = False
    database_url: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BtcExperienceRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            enabled=_bool(source.get(ENV_EXPERIENCE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
        ).validated()

    def validated(self) -> "BtcExperienceRuntimeConfig":
        if self.enabled and not self.database_url:
            raise ValueError("BTC Experience Memory persistence cannot run without DATABASE_URL")
        return self


def build_btc_experience_runtime(config: BtcExperienceRuntimeConfig) -> dict:
    config = config.validated()
    if not config.enabled:
        return {
            "status": "BTC_EXPERIENCE_RUNTIME_DISABLED",
            "config": config,
            "store": None,
        }
    return {
        "status": "BTC_EXPERIENCE_RUNTIME_READY",
        "config": config,
        "store": PostgresBtcExperienceStore(config.database_url),
    }


async def initialize_btc_experience_runtime(config: BtcExperienceRuntimeConfig) -> dict:
    runtime = build_btc_experience_runtime(config)
    if runtime["store"] is None:
        return {
            "status": "BTC_EXPERIENCE_RUNTIME_DISABLED",
            "schema_initialized": False,
            "collection_started": False,
            "execution_started": False,
        }
    initialized = await runtime["store"].initialize()
    return {
        "status": runtime["status"],
        "schema_initialized": initialized["status"] == "BTC_EXPERIENCE_POSTGRES_SCHEMA_READY",
        "collection_started": False,
        "execution_started": False,
    }


async def persist_resolved_experience(
    config: BtcExperienceRuntimeConfig,
    *,
    entry: dict,
    resolved_at: datetime,
) -> dict:
    runtime = build_btc_experience_runtime(config)
    if runtime["store"] is None:
        return {
            "status": "BTC_EXPERIENCE_PERSISTENCE_DISABLED",
            "persisted": False,
            "trade_generated": False,
        }
    record = resolved_experience_record_from_entry(entry=entry, resolved_at=resolved_at)
    result = await runtime["store"].insert_resolved(record)
    return {
        "status": result["status"],
        "persisted": result["status"] in {"INSERTED_RESOLVED_EXPERIENCE", "IDEMPOTENT_RESOLVED_EXPERIENCE"},
        "natural_key": result["natural_key"],
        "record_fingerprint": result["record_fingerprint"],
        "trade_generated": False,
    }


def runtime_status(config: BtcExperienceRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "BTC_EXPERIENCE_RUNTIME_STATUS_V1",
        "enabled": config.enabled,
        "database_configured": bool(config.database_url),
        "automatic_startup_registration": False,
        "automatic_collection": False,
        "automatic_network_request": False,
        "market_data_capture_switch_enables_experience": False,
        "unresolved_case_auto_promoted": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_EXPERIENCE_RUNTIME_V1",
        "enabled_by_default": False,
        "database_required_when_enabled": True,
        "automatic_startup_registration": False,
        "schema_initialization_starts_collection": False,
        "schema_initialization_starts_execution": False,
        "market_data_capture_switch_enables_experience": False,
        "resolved_entry_and_resolved_at_required": True,
        "unresolved_case_auto_promoted": False,
        "market_data_pit_archive_used_for_outcomes": False,
        "trade_generation_enabled": False,
        "research_only": True,
    }
