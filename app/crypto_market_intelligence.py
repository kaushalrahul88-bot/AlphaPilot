"""Crypto Market Brain evidence interpretation, research/shadow only.

This layer consumes already-observed information and produces instrument-neutral
market evidence. It never creates an Options or Futures order. Futures/perpetual
metrics may therefore inform the shared BTC/ETH/etc market state without being
allowed to leak a futures trade into the Options route.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

Direction = Literal["BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"]
EvidenceStrength = Literal["LOW", "MEDIUM", "HIGH"]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class MarketObservation:
    asset: str
    family: str
    observed_at: datetime
    source: str
    source_tier: str
    value: float | str | None
    unit: str | None = None
    direction_hint: Direction = "UNKNOWN"
    confidence: float = 0.5
    horizon: str = "intraday"
    causal_origin: str = "UNKNOWN"
    verified: bool = True
    metadata: dict | None = None


@dataclass(frozen=True)
class Evidence:
    family: str
    causal_origin: str
    stance: Direction
    strength: EvidenceStrength
    confidence: float
    observed_at: datetime
    reason: str
    context_only: bool
    source: str
    metadata: dict


def classify_price_oi_state(price_change_pct: float, oi_change_pct: float) -> str:
    if price_change_pct > 0 and oi_change_pct > 0:
        return "PRICE_UP_OI_UP"
    if price_change_pct > 0 and oi_change_pct < 0:
        return "PRICE_UP_OI_DOWN"
    if price_change_pct < 0 and oi_change_pct > 0:
        return "PRICE_DOWN_OI_UP"
    if price_change_pct < 0 and oi_change_pct < 0:
        return "PRICE_DOWN_OI_DOWN"
    return "MIXED_OR_FLAT"


def derivatives_context(
    *,
    observed_at: datetime,
    price_change_pct: float,
    oi_change_pct: float,
    funding_percentile: float | None,
    short_liquidations_usd: float = 0.0,
    long_liquidations_usd: float = 0.0,
    perp_premium_bps: float | None = None,
    source: str = "GLOBAL_DERIVATIVES",
) -> Evidence:
    """Interpret a derivatives state without emitting an instrument-specific trade."""
    state = classify_price_oi_state(price_change_pct, oi_change_pct)
    funding_p = None if funding_percentile is None else _bounded(funding_percentile)
    short_squeeze = price_change_pct > 0 and oi_change_pct < 0 and short_liquidations_usd > long_liquidations_usd
    long_flush = price_change_pct < 0 and oi_change_pct < 0 and long_liquidations_usd > short_liquidations_usd
    crowded_long = (
        price_change_pct > 0 and oi_change_pct > 0 and funding_p is not None and funding_p >= 0.90
    )
    crowded_short = (
        price_change_pct < 0 and oi_change_pct > 0 and funding_p is not None and funding_p <= 0.10
    )

    stance: Direction = "UNKNOWN"
    strength: EvidenceStrength = "LOW"
    reason = f"Derivatives state {state}; requires context."

    if short_squeeze:
        stance, strength = "BULLISH", "MEDIUM"
        reason = "Price rose while OI fell and short liquidations dominated: squeeze-driven bullish impulse; continuation quality is uncertain."
    elif long_flush:
        stance, strength = "BEARISH", "MEDIUM"
        reason = "Price fell while OI fell and long liquidations dominated: deleveraging/long-flush bearish impulse; continuation quality is uncertain."
    elif crowded_long:
        stance, strength = "NEUTRAL", "HIGH"
        reason = "Price and OI rose with extreme positive funding percentile: long crowding raises liquidation risk and reduces chase quality."
    elif crowded_short:
        stance, strength = "NEUTRAL", "HIGH"
        reason = "Price fell with rising OI and extreme negative funding percentile: short crowding raises squeeze risk and reduces chase quality."
    elif price_change_pct > 0 and oi_change_pct > 0:
        stance, strength = "BULLISH", "MEDIUM"
        reason = "Price and OI rose together without detected funding extreme: fresh leveraged participation supports the bullish state conditionally."
    elif price_change_pct < 0 and oi_change_pct > 0:
        stance, strength = "BEARISH", "MEDIUM"
        reason = "Price fell while OI rose without detected funding extreme: fresh bearish exposure supports the bearish state conditionally."

    metadata = {
        "state": state,
        "price_change_pct": price_change_pct,
        "oi_change_pct": oi_change_pct,
        "funding_percentile": funding_p,
        "short_liquidations_usd": short_liquidations_usd,
        "long_liquidations_usd": long_liquidations_usd,
        "perp_premium_bps": perp_premium_bps,
        "short_squeeze": short_squeeze,
        "long_flush": long_flush,
        "crowded_long": crowded_long,
        "crowded_short": crowded_short,
        "may_inform_options": True,
        "may_generate_futures_trade": False,
    }
    return Evidence(
        family="DERIVATIVES_POSITIONING",
        causal_origin="LEVERAGED_POSITIONING",
        stance=stance,
        strength=strength,
        confidence=0.75 if stance != "UNKNOWN" else 0.5,
        observed_at=_utc(observed_at),
        reason=reason,
        context_only=False,
        source=source,
        metadata=metadata,
    )


def onchain_transfer_context(
    observation: MarketObservation,
    *,
    entity_confidence: float,
    destination_type: str,
    historical_directional_reliability: float = 0.5,
    market_confirmation: bool = False,
) -> Evidence:
    """Interpret a transfer conservatively; raw whale alerts remain context-only."""
    entity_conf = _bounded(entity_confidence)
    historical = _bounded(historical_directional_reliability)
    destination = str(destination_type or "UNKNOWN").upper()
    source_tier = str(observation.source_tier or "E_UNVERIFIED").upper()
    verified = bool(observation.verified)

    can_directionally_interpret = (
        verified
        and entity_conf >= 0.75
        and historical >= 0.65
        and market_confirmation
        and destination in {"SPOT_EXCHANGE", "SELF_CUSTODY", "KNOWN_CUSTODIAN"}
        and source_tier not in {"D_COMMUNITY", "E_UNVERIFIED"}
    )

    stance: Direction = "UNKNOWN"
    reason = "On-chain movement is not a trade by itself; entity/destination/history/market confirmation are insufficient."
    if can_directionally_interpret and destination == "SPOT_EXCHANGE":
        stance = "BEARISH"
        reason = "Verified entity moved assets to a spot exchange and the entity has historically reliable sell-side behaviour with market confirmation."
    elif can_directionally_interpret and destination == "SELF_CUSTODY":
        stance = "BULLISH"
        reason = "Verified exchange-origin withdrawal to self-custody aligns with historically reliable supply-removal behaviour and market confirmation."
    elif can_directionally_interpret:
        stance = "NEUTRAL"
        reason = "Verified custody movement is informative but not directionally clean."

    confidence = _bounded(observation.confidence) * entity_conf * (0.6 + 0.4 * historical)
    return Evidence(
        family="ONCHAIN_FLOW",
        causal_origin="BLOCKCHAIN_ENTITY_FLOW",
        stance=stance,
        strength="MEDIUM" if can_directionally_interpret else "LOW",
        confidence=round(confidence, 4),
        observed_at=_utc(observation.observed_at),
        reason=reason,
        context_only=not can_directionally_interpret,
        source=observation.source,
        metadata={
            "destination_type": destination,
            "entity_confidence": entity_conf,
            "historical_directional_reliability": historical,
            "market_confirmation": market_confirmation,
            "verified": verified,
            "source_tier": source_tier,
            "raw_value": observation.value,
        },
    )


def stablecoin_liquidity_context(
    *,
    observed_at: datetime,
    netflow_usd: float,
    venue_type: str,
    source: str,
    confidence: float = 0.7,
) -> Evidence:
    venue = str(venue_type or "UNKNOWN").upper()
    if venue == "SPOT_EXCHANGE" and netflow_usd > 0:
        stance: Direction = "BULLISH"
        reason = "Positive stablecoin netflow to spot venues can increase deployable buying liquidity; treat as conditional context."
        context_only = False
    elif venue == "SPOT_EXCHANGE" and netflow_usd < 0:
        stance = "BEARISH"
        reason = "Stablecoin net outflow from spot venues can reduce immediately deployable buying liquidity; treat as conditional context."
        context_only = False
    else:
        stance = "UNKNOWN"
        reason = "Stablecoin flow to derivatives/unknown venues may be collateral for either direction and is context-only."
        context_only = True
    return Evidence(
        family="STABLECOIN_LIQUIDITY",
        causal_origin="CRYPTO_DOLLAR_LIQUIDITY",
        stance=stance,
        strength="MEDIUM" if not context_only else "LOW",
        confidence=_bounded(confidence),
        observed_at=_utc(observed_at),
        reason=reason,
        context_only=context_only,
        source=source,
        metadata={"netflow_usd": netflow_usd, "venue_type": venue},
    )


HORIZON_MAX_AGE_SECONDS = {
    "scalp": 15 * 60,
    "intraday": 6 * 60 * 60,
    "swing": 3 * 24 * 60 * 60,
    "position": 30 * 24 * 60 * 60,
}


def evidence_is_fresh(evidence: Evidence, *, decision_at: datetime, trade_horizon: str) -> bool:
    max_age = HORIZON_MAX_AGE_SECONDS.get(trade_horizon)
    if max_age is None:
        raise ValueError(f"unsupported trade_horizon: {trade_horizon}")
    age = (_utc(decision_at) - _utc(evidence.observed_at)).total_seconds()
    return 0 <= age <= max_age


def assemble_market_state(
    evidence: list[Evidence],
    *,
    decision_at: datetime,
    trade_horizon: str,
) -> dict:
    """Build an instrument-neutral thesis with causal-origin de-duplication.

    Two aligned independent causal origins are required for direction. Context-only
    evidence can annotate the state but cannot manufacture confirmation.
    """
    eligible = [row for row in evidence if evidence_is_fresh(row, decision_at=decision_at, trade_horizon=trade_horizon)]
    counted_by_origin: dict[str, Evidence] = {}
    context: list[Evidence] = []
    duplicates: list[Evidence] = []

    for row in eligible:
        if row.context_only or row.stance not in {"BULLISH", "BEARISH"}:
            context.append(row)
            continue
        if row.causal_origin in counted_by_origin:
            duplicates.append(row)
            continue
        counted_by_origin[row.causal_origin] = row

    counted = list(counted_by_origin.values())
    bullish = [row for row in counted if row.stance == "BULLISH"]
    bearish = [row for row in counted if row.stance == "BEARISH"]
    if bullish and bearish:
        direction = "UNKNOWN"
        state = "INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION"
    elif len(bullish) >= 2:
        direction = "BULLISH"
        state = "COHERENT_DIRECTION_THESIS"
    elif len(bearish) >= 2:
        direction = "BEARISH"
        state = "COHERENT_DIRECTION_THESIS"
    else:
        direction = "UNKNOWN"
        state = "INSUFFICIENT_INDEPENDENT_CONFIRMATION"

    return {
        "version": "CRYPTO_MARKET_STATE_V1",
        "instrument_neutral": True,
        "direction": direction,
        "state": state,
        "trade_horizon": trade_horizon,
        "decision_at": _utc(decision_at).isoformat(),
        "counted_evidence": [asdict(row) for row in counted],
        "context_evidence": [asdict(row) for row in context],
        "duplicate_origin_evidence": [asdict(row) for row in duplicates],
        "options_trade_generated": False,
        "futures_trade_generated": False,
    }
