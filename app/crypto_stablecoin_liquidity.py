"""Aggregate stablecoin-liquidity state from point-in-time first-seen snapshots.

This module answers whether the broad USD-stablecoin supply base is expanding,
contracting or roughly stable. It deliberately does not infer exchange buying
power or emit bullish/bearish trade direction from aggregate supply alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Iterable, Literal

from app.crypto_market_intelligence import Evidence
from app.crypto_stablecoin_pit_capture import STABLECOIN_SUPPLY_DATASET

LiquidityState = Literal["EXPANDING", "CONTRACTING", "STABLE", "UNKNOWN"]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _stamp(value) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value)))


@dataclass(frozen=True)
class StablecoinLiquidityPolicy:
    comparison_hours: int = 24
    stable_band_pct: float = 0.10
    max_snapshot_age_seconds: int = 2 * 60 * 60

    def validated(self) -> "StablecoinLiquidityPolicy":
        if int(self.comparison_hours) < 1:
            raise ValueError("comparison_hours must be >= 1")
        band = float(self.stable_band_pct)
        if not isfinite(band) or band < 0:
            raise ValueError("stable_band_pct must be finite and >= 0")
        if int(self.max_snapshot_age_seconds) < 1:
            raise ValueError("max_snapshot_age_seconds must be >= 1")
        return self


def _supply(row: dict) -> float | None:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    try:
        value = float(payload["total_circulating"])
    except (KeyError, TypeError, ValueError):
        return None
    return value if isfinite(value) and value > 0 else None


def aggregate_stablecoin_liquidity_context(
    records: Iterable[dict],
    *,
    decision_at: datetime,
    policy: StablecoinLiquidityPolicy | None = None,
) -> Evidence:
    policy = (policy or StablecoinLiquidityPolicy()).validated()
    decision = _utc(decision_at)
    visible: list[tuple[datetime, dict, float]] = []
    excluded_future = 0
    excluded_invalid = 0
    for row in records:
        if row.get("dataset") != STABLECOIN_SUPPLY_DATASET or row.get("first_seen_at") is None:
            continue
        seen = _stamp(row["first_seen_at"])
        if seen > decision:
            excluded_future += 1
            continue
        supply = _supply(row)
        if supply is None:
            excluded_invalid += 1
            continue
        visible.append((seen, row, supply))

    visible.sort(key=lambda item: item[0])
    metadata = {
        "liquidity_state": "UNKNOWN",
        "comparison_hours": policy.comparison_hours,
        "stable_band_pct": policy.stable_band_pct,
        "excluded_future_rows": excluded_future,
        "excluded_invalid_rows": excluded_invalid,
        "aggregate_supply_equals_exchange_inflow": False,
        "aggregate_supply_equals_deployable_spot_buying_power": False,
        "venue_specific_flow_confirmation_present": False,
        "standalone_direction_allowed": False,
        "may_inform_options": True,
        "may_generate_trade": False,
    }
    if not visible:
        return Evidence(
            family="STABLECOIN_LIQUIDITY",
            causal_origin="CRYPTO_DOLLAR_LIQUIDITY",
            stance="UNKNOWN",
            strength="LOW",
            confidence=0.35,
            observed_at=decision,
            reason="No point-in-time aggregate stablecoin supply snapshot is available by the decision time.",
            context_only=True,
            source="DEFILLAMA_PIT",
            metadata=metadata,
        )

    latest_seen, latest_row, latest_supply = visible[-1]
    metadata.update({
        "latest_first_seen_at": latest_seen.isoformat(),
        "latest_total_circulating": latest_supply,
        "latest_source_key": latest_row.get("source_key"),
    })
    age = (decision - latest_seen).total_seconds()
    if age > policy.max_snapshot_age_seconds:
        metadata["snapshot_age_seconds"] = age
        return Evidence(
            family="STABLECOIN_LIQUIDITY",
            causal_origin="CRYPTO_DOLLAR_LIQUIDITY",
            stance="UNKNOWN",
            strength="LOW",
            confidence=0.4,
            observed_at=latest_seen,
            reason="Latest aggregate stablecoin supply snapshot is stale for the configured decision horizon.",
            context_only=True,
            source="DEFILLAMA_PIT",
            metadata=metadata,
        )

    target = latest_seen - timedelta(hours=policy.comparison_hours)
    prior_candidates = [(seen, row, supply) for seen, row, supply in visible[:-1] if seen <= target]
    if not prior_candidates:
        return Evidence(
            family="STABLECOIN_LIQUIDITY",
            causal_origin="CRYPTO_DOLLAR_LIQUIDITY",
            stance="UNKNOWN",
            strength="LOW",
            confidence=0.45,
            observed_at=latest_seen,
            reason="Aggregate stablecoin supply is available, but there is insufficient prior point-in-time history for the requested comparison horizon.",
            context_only=True,
            source="DEFILLAMA_PIT",
            metadata=metadata,
        )

    prior_seen, prior_row, prior_supply = prior_candidates[-1]
    change_pct = ((latest_supply - prior_supply) / prior_supply) * 100.0
    if change_pct > policy.stable_band_pct:
        state: LiquidityState = "EXPANDING"
        reason = "Aggregate USD-stablecoin supply expanded over the comparison horizon; this indicates broader crypto-dollar liquidity capacity, not confirmed exchange buying flow."
    elif change_pct < -policy.stable_band_pct:
        state = "CONTRACTING"
        reason = "Aggregate USD-stablecoin supply contracted over the comparison horizon; this indicates a smaller crypto-dollar liquidity base, not a standalone bearish trade signal."
    else:
        state = "STABLE"
        reason = "Aggregate USD-stablecoin supply was broadly stable over the comparison horizon."

    metadata.update({
        "liquidity_state": state,
        "prior_first_seen_at": prior_seen.isoformat(),
        "prior_total_circulating": prior_supply,
        "prior_source_key": prior_row.get("source_key"),
        "supply_change_pct": round(change_pct, 8),
        "snapshot_age_seconds": age,
    })
    return Evidence(
        family="STABLECOIN_LIQUIDITY",
        causal_origin="CRYPTO_DOLLAR_LIQUIDITY",
        stance="UNKNOWN",
        strength="MEDIUM" if state != "STABLE" else "LOW",
        confidence=0.65,
        observed_at=latest_seen,
        reason=reason,
        context_only=True,
        source="DEFILLAMA_PIT",
        metadata=metadata,
    )


def architecture_contract() -> dict:
    return {
        "version": "AGGREGATE_STABLECOIN_LIQUIDITY_V1",
        "states": ["EXPANDING", "CONTRACTING", "STABLE", "UNKNOWN"],
        "point_in_time_first_seen_only": True,
        "future_snapshots_allowed": False,
        "aggregate_supply_equals_exchange_inflow": False,
        "aggregate_supply_equals_deployable_spot_buying_power": False,
        "aggregate_supply_may_emit_bullish_bearish_stance": False,
        "venue_specific_flow_is_separate_dataset": True,
        "context_only": True,
        "trade_generation_allowed": False,
        "research_only": True,
    }
