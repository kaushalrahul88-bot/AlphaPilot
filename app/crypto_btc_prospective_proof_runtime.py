"""Environment-gated configuration for prospective BTC underlying proof persistence.

This runtime contract only selects the insert-only thesis tape backend and freezes
its evaluation policy. It does not start collection, schedule decisions, resolve
outcomes, generate trades, or deploy capital.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from math import isfinite
from typing import Mapping

from app.crypto_btc_prospective_thesis_tape import ProspectiveBtcThesisTapePolicy

ENV_PROOF_POSTGRES_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PROSPECTIVE_THESIS_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_HORIZON_HOURS = "ALPHAPILOT_CRYPTO_BTC_PROSPECTIVE_HORIZON_HOURS"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


def _float(value: str | None, default: float) -> float:
    if value is None or not str(value).strip():
        return float(default)
    try:
        number = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"invalid float environment value: {value!r}") from exc
    if not isfinite(number):
        raise ValueError(f"environment float must be finite: {value!r}")
    return number


@dataclass(frozen=True)
class BtcProspectiveProofRuntimeConfig:
    postgres_enabled: bool = False
    database_url: str = ""
    evaluation_horizon_hours: float = 4.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BtcProspectiveProofRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            postgres_enabled=_bool(source.get(ENV_PROOF_POSTGRES_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            evaluation_horizon_hours=_float(source.get(ENV_HORIZON_HOURS), 4.0),
        ).validated()

    def validated(self) -> "BtcProspectiveProofRuntimeConfig":
        if self.postgres_enabled and not self.database_url:
            raise ValueError("prospective BTC thesis Postgres enabled but DATABASE_URL is missing")
        self.tape_policy().validated()
        return self

    def tape_policy(self) -> ProspectiveBtcThesisTapePolicy:
        return ProspectiveBtcThesisTapePolicy(
            trade_horizon="intraday",
            evaluation_horizon_hours=float(self.evaluation_horizon_hours),
            terminal_price_max_gap_seconds=60,
            neutral_band_pct=0.25,
            large_move_threshold_pct=1.5,
        )


def architecture_contract() -> dict:
    return {
        "version": "BTC_PROSPECTIVE_PROOF_RUNTIME_CONTRACT_V1",
        "postgres_enabled_by_default": False,
        "database_url_required_when_enabled": True,
        "default_evaluation_horizon_hours": 4.0,
        "schema_init_starts_market_collection": False,
        "schema_init_freezes_decision": False,
        "automatic_decision_scheduler": False,
        "automatic_outcome_resolution": False,
        "caller_may_backdate_decision": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "research_only": True,
    }
