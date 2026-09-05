"""On-chain metric semantics and horizon guardrails for the Crypto Brain.

This module deliberately treats on-chain metrics as context by default. It
prevents cycle metrics such as MVRV/SOPR, and faster entity-flow metrics such as
exchange/whale transfers, from silently becoming standalone short-horizon trade
signals without empirical promotion and market confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

MetricRole = Literal["FAST_EVENT", "STATE_CONTEXT", "CYCLE_CONTEXT", "SUPPLY_EVENT"]
Direction = Literal["BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"]


@dataclass(frozen=True)
class OnchainMetric:
    asset: str
    metric: str
    observed_at: datetime
    value: float
    source: str
    role: MetricRole
    unit: str | None = None
    historical_percentile: float | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class OnchainContext:
    family: str
    metric: str
    stance: Direction
    context_only: bool
    horizon: str
    reason: str
    observed_at: datetime
    source: str
    metadata: dict


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _percentile(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


METRIC_SEMANTICS = {
    "SOPR": {
        "role": "STATE_CONTEXT",
        "horizon": "hours_to_weeks",
        "standalone_direction_allowed": False,
        "meaning": "Realized profit/loss state of spent coins; below/above 1 requires cohort/regime context.",
    },
    "MVRV": {
        "role": "CYCLE_CONTEXT",
        "horizon": "days_to_months",
        "standalone_direction_allowed": False,
        "meaning": "Market value relative to realized value; useful for broad profitability/cycle context, not intraday timing.",
    },
    "EXCHANGE_NETFLOW": {
        "role": "FAST_EVENT",
        "horizon": "minutes_to_days",
        "standalone_direction_allowed": False,
        "meaning": "Exchange netflow can indicate changes in liquid supply, but labeled exchange entities can be revised and flow intent is ambiguous; require first-seen provenance plus spot/derivatives confirmation.",
    },
    "WHALE_EXCHANGE_FLOW": {
        "role": "FAST_EVENT",
        "horizon": "minutes_to_days",
        "standalone_direction_allowed": False,
        "meaning": "Large-holder transfers to exchanges may represent potential sell-side liquidity but can also reflect custody, collateral, OTC or market-making activity; never infer selling from transfer size alone.",
    },
    "REALIZED_PRICE": {
        "role": "CYCLE_CONTEXT",
        "horizon": "days_to_months",
        "standalone_direction_allowed": False,
        "meaning": "Aggregate on-chain cost-basis reference; not a short-horizon trigger.",
    },
    "LTH_SUPPLY": {
        "role": "STATE_CONTEXT",
        "horizon": "days_to_months",
        "standalone_direction_allowed": False,
        "meaning": "Long-term-holder supply state; accumulation/distribution must be inferred from changes and spending behaviour.",
    },
    "STH_REALIZED_LOSS": {
        "role": "STATE_CONTEXT",
        "horizon": "hours_to_weeks",
        "standalone_direction_allowed": False,
        "meaning": "Short-term-holder realized loss can identify stress/capitulation context but not reversal timing by itself.",
    },
    "MINER_EXCHANGE_FLOW": {
        "role": "FAST_EVENT",
        "horizon": "minutes_to_days",
        "standalone_direction_allowed": False,
        "meaning": "Miner-to-exchange flow can indicate potential supply but requires entity and market confirmation.",
    },
    "VALIDATOR_EXIT_QUEUE": {
        "role": "STATE_CONTEXT",
        "horizon": "hours_to_weeks",
        "standalone_direction_allowed": False,
        "meaning": "Validator exits affect staking/liquidity context and require chain-specific interpretation.",
    },
    "ACTIVE_ADDRESSES": {
        "role": "STATE_CONTEXT",
        "horizon": "hours_to_weeks",
        "standalone_direction_allowed": False,
        "meaning": "Network activity can be distorted by bots, airdrops, spam or exchange operations.",
    },
    "TOKEN_UNLOCK": {
        "role": "SUPPLY_EVENT",
        "horizon": "hours_to_months",
        "standalone_direction_allowed": False,
        "meaning": "Unlocks increase transferable supply but recipient incentives, hedging, prior pricing and liquidity determine price impact.",
    },
}


def metric_semantics(metric: str) -> dict:
    key = str(metric or "").upper()
    if key not in METRIC_SEMANTICS:
        return {
            "role": "STATE_CONTEXT",
            "horizon": "unknown",
            "standalone_direction_allowed": False,
            "meaning": "Unknown on-chain metric; discovery/context only until explicitly registered and validated.",
        }
    return dict(METRIC_SEMANTICS[key])


def generic_metric_context(metric: OnchainMetric) -> OnchainContext:
    semantics = metric_semantics(metric.metric)
    return OnchainContext(
        family="ONCHAIN_METRIC",
        metric=metric.metric.upper(),
        stance="UNKNOWN",
        context_only=True,
        horizon=semantics["horizon"],
        reason=semantics["meaning"],
        observed_at=_utc(metric.observed_at),
        source=metric.source,
        metadata={
            "value": metric.value,
            "unit": metric.unit,
            "role": semantics["role"],
            "historical_percentile": _percentile(metric.historical_percentile),
            "standalone_direction_allowed": False,
            "raw_metadata": dict(metric.metadata or {}),
        },
    )


def token_unlock_context(
    *,
    asset: str,
    observed_at: datetime,
    unlock_pct_circulating: float,
    source: str,
    recipient_type: str = "UNKNOWN",
    already_priced_confidence: float = 0.5,
) -> OnchainContext:
    size = max(0.0, float(unlock_pct_circulating))
    large = size >= 5.0
    return OnchainContext(
        family="TOKEN_SUPPLY",
        metric="TOKEN_UNLOCK",
        stance="UNKNOWN",
        context_only=True,
        horizon="hours_to_months",
        reason=(
            "Large token unlock supply event; potential dilution/overhang must be tested against recipient behaviour, hedging, liquidity and prior pricing."
            if large
            else "Token unlock is a supply-context event; size alone does not justify a directional trade."
        ),
        observed_at=_utc(observed_at),
        source=source,
        metadata={
            "asset": asset.upper(),
            "unlock_pct_circulating": size,
            "large_unlock": large,
            "recipient_type": str(recipient_type or "UNKNOWN").upper(),
            "already_priced_confidence": max(0.0, min(1.0, float(already_priced_confidence))),
            "standalone_direction_allowed": False,
        },
    )


def onchain_architecture_contract() -> dict:
    return {
        "version": "CRYPTO_ONCHAIN_INTELLIGENCE_V2",
        "raw_transfer_equals_trade": False,
        "exchange_inflow_equals_sell": False,
        "whale_transfer_equals_sell": False,
        "slow_cycle_metric_may_trigger_intraday_trade": False,
        "network_activity_is_directional_by_itself": False,
        "miner_or_validator_flow_is_directional_by_itself": False,
        "token_unlock_size_is_directional_by_itself": False,
        "historical_percentiles_supported": True,
        "asset_specific_interpretation_required": True,
        "failed_signal_learning_required": True,
        "registered_metrics": {name: dict(meta) for name, meta in METRIC_SEMANTICS.items()},
        "research_only": True,
        "broker_execution_enabled": False,
    }
