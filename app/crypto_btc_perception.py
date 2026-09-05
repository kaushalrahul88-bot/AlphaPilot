"""BTC specialist perception for the research-only Crypto Market Brain.

This module converts BTC-specific market observations into instrument-neutral
Evidence and assembles them through the shared Crypto Market Brain. It never
creates an Options or Futures trade. In particular, options-market information
is positioning/translation context only so an options chain cannot manufacture
the BTC direction that is later used to select an option.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from app.crypto_market_intelligence import Evidence, assemble_market_state

Direction = Literal["BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class BtcSpotStructureSnapshot:
    observed_at: datetime
    price: float
    return_1h_pct: float
    return_4h_pct: float
    return_24h_pct: float
    close_location: float
    volume_percentile: float
    breakout_state: str = "NONE"
    source: str = "BTC_GLOBAL_SPOT"


@dataclass(frozen=True)
class BtcOptionsMarketSnapshot:
    observed_at: datetime
    atm_iv_percentile: float | None
    put_call_skew_25d: float | None
    put_call_oi_ratio: float | None
    term_structure_slope: float | None = None
    source: str = "BTC_GLOBAL_OPTIONS"


@dataclass(frozen=True)
class BtcMacroCrossAssetSnapshot:
    observed_at: datetime
    dxy_change_pct: float | None = None
    nasdaq_change_pct: float | None = None
    real_yield_change_bps: float | None = None
    gold_change_pct: float | None = None
    source: str = "GLOBAL_MACRO_CROSS_ASSET"


@dataclass(frozen=True)
class BtcHistoricalAnalogue:
    observed_at: datetime
    analogue_count: int
    bullish_fraction: float | None
    similarity: float | None
    source: str = "BTC_HISTORICAL_MEMORY"


def spot_structure_context(snapshot: BtcSpotStructureSnapshot) -> Evidence:
    """Create the primary BTC observable-price lane without using future bars."""
    close_location = _bounded(snapshot.close_location)
    volume_percentile = _bounded(snapshot.volume_percentile)
    breakout = str(snapshot.breakout_state or "NONE").upper()

    bullish_alignment = (
        snapshot.return_1h_pct > 0
        and snapshot.return_4h_pct > 0
        and snapshot.return_24h_pct > 0
        and close_location >= 0.65
    )
    bearish_alignment = (
        snapshot.return_1h_pct < 0
        and snapshot.return_4h_pct < 0
        and snapshot.return_24h_pct < 0
        and close_location <= 0.35
    )
    bullish_breakout = breakout in {"UPSIDE_CONFIRMED", "UPSIDE_RETEST_HELD"}
    bearish_breakout = breakout in {"DOWNSIDE_CONFIRMED", "DOWNSIDE_RETEST_HELD"}

    stance: Direction = "UNKNOWN"
    strength = "LOW"
    reason = "BTC spot structure is mixed or lacks sufficient multi-horizon confirmation."
    if bullish_alignment and bullish_breakout and volume_percentile >= 0.55:
        stance, strength = "BULLISH", "HIGH"
        reason = "BTC spot structure is bullish across 1h/4h/24h with a confirmed upside break and adequate participation."
    elif bearish_alignment and bearish_breakout and volume_percentile >= 0.55:
        stance, strength = "BEARISH", "HIGH"
        reason = "BTC spot structure is bearish across 1h/4h/24h with a confirmed downside break and adequate participation."
    elif bullish_alignment and volume_percentile >= 0.45:
        stance, strength = "BULLISH", "MEDIUM"
        reason = "BTC 1h/4h/24h returns and close location align bullishly with acceptable spot participation."
    elif bearish_alignment and volume_percentile >= 0.45:
        stance, strength = "BEARISH", "MEDIUM"
        reason = "BTC 1h/4h/24h returns and close location align bearishly with acceptable spot participation."

    return Evidence(
        family="BTC_SPOT_STRUCTURE",
        causal_origin="SPOT_PRICE_STRUCTURE",
        stance=stance,
        strength=strength,
        confidence=0.85 if strength == "HIGH" else 0.72 if strength == "MEDIUM" else 0.5,
        observed_at=_utc(snapshot.observed_at),
        reason=reason,
        context_only=False if stance in {"BULLISH", "BEARISH"} else True,
        source=snapshot.source,
        metadata={
            "price": snapshot.price,
            "return_1h_pct": snapshot.return_1h_pct,
            "return_4h_pct": snapshot.return_4h_pct,
            "return_24h_pct": snapshot.return_24h_pct,
            "close_location": close_location,
            "volume_percentile": volume_percentile,
            "breakout_state": breakout,
            "instrument_neutral": True,
            "trade_generated": False,
        },
    )


def options_market_context(snapshot: BtcOptionsMarketSnapshot) -> Evidence:
    """Describe BTC options positioning without allowing it to create direction."""
    iv_p = None if snapshot.atm_iv_percentile is None else _bounded(snapshot.atm_iv_percentile)
    skew = snapshot.put_call_skew_25d
    oi_ratio = snapshot.put_call_oi_ratio

    tags: list[str] = []
    if iv_p is not None and iv_p >= 0.90:
        tags.append("IV_EXTREME_HIGH")
    elif iv_p is not None and iv_p <= 0.10:
        tags.append("IV_EXTREME_LOW")
    if skew is not None and skew >= 5.0:
        tags.append("PUT_SKEW_ELEVATED")
    elif skew is not None and skew <= -5.0:
        tags.append("CALL_SKEW_ELEVATED")
    if oi_ratio is not None and oi_ratio >= 1.4:
        tags.append("PUT_OI_HEAVY")
    elif oi_ratio is not None and oi_ratio <= 0.7:
        tags.append("CALL_OI_HEAVY")

    return Evidence(
        family="BTC_OPTIONS_MARKET",
        causal_origin="OPTIONS_POSITIONING",
        stance="UNKNOWN",
        strength="MEDIUM" if tags else "LOW",
        confidence=0.75 if tags else 0.6,
        observed_at=_utc(snapshot.observed_at),
        reason="BTC options IV/skew/OI are translation and positioning context; they cannot independently decide underlying BTC direction.",
        context_only=True,
        source=snapshot.source,
        metadata={
            "atm_iv_percentile": iv_p,
            "put_call_skew_25d": skew,
            "put_call_oi_ratio": oi_ratio,
            "term_structure_slope": snapshot.term_structure_slope,
            "tags": tags,
            "standalone_direction_allowed": False,
            "may_inform_options_translation": True,
            "may_generate_options_trade": False,
            "may_generate_futures_trade": False,
        },
    )


def macro_cross_asset_context(snapshot: BtcMacroCrossAssetSnapshot) -> Evidence:
    """Interpret only unusually coherent macro/risk alignment as independent context."""
    bullish_votes = 0
    bearish_votes = 0
    observed = 0

    if snapshot.dxy_change_pct is not None:
        observed += 1
        if snapshot.dxy_change_pct <= -0.35:
            bullish_votes += 1
        elif snapshot.dxy_change_pct >= 0.35:
            bearish_votes += 1
    if snapshot.nasdaq_change_pct is not None:
        observed += 1
        if snapshot.nasdaq_change_pct >= 0.75:
            bullish_votes += 1
        elif snapshot.nasdaq_change_pct <= -0.75:
            bearish_votes += 1
    if snapshot.real_yield_change_bps is not None:
        observed += 1
        if snapshot.real_yield_change_bps <= -5.0:
            bullish_votes += 1
        elif snapshot.real_yield_change_bps >= 5.0:
            bearish_votes += 1

    stance: Direction = "UNKNOWN"
    context_only = True
    strength = "LOW"
    if observed >= 2 and bullish_votes >= 2 and bearish_votes == 0:
        stance, context_only, strength = "BULLISH", False, "MEDIUM"
    elif observed >= 2 and bearish_votes >= 2 and bullish_votes == 0:
        stance, context_only, strength = "BEARISH", False, "MEDIUM"

    return Evidence(
        family="BTC_MACRO_CROSS_ASSET",
        causal_origin="GLOBAL_RISK_LIQUIDITY",
        stance=stance,
        strength=strength,
        confidence=0.7 if not context_only else 0.55,
        observed_at=_utc(snapshot.observed_at),
        reason=(
            "Independent macro/risk inputs are coherently aligned for BTC."
            if not context_only
            else "Macro/cross-asset inputs are incomplete or mixed and remain contextual."
        ),
        context_only=context_only,
        source=snapshot.source,
        metadata={
            "dxy_change_pct": snapshot.dxy_change_pct,
            "nasdaq_change_pct": snapshot.nasdaq_change_pct,
            "real_yield_change_bps": snapshot.real_yield_change_bps,
            "gold_change_pct": snapshot.gold_change_pct,
            "bullish_votes": bullish_votes,
            "bearish_votes": bearish_votes,
            "instrument_neutral": True,
        },
    )


def historical_analogue_context(analogue: BtcHistoricalAnalogue) -> Evidence:
    """Historical memory describes precedent but never supplies a fresh causal vote."""
    similarity = None if analogue.similarity is None else _bounded(analogue.similarity)
    bullish_fraction = None if analogue.bullish_fraction is None else _bounded(analogue.bullish_fraction)
    return Evidence(
        family="BTC_HISTORICAL_ANALOGUE",
        causal_origin="HISTORICAL_MEMORY",
        stance="UNKNOWN",
        strength="MEDIUM" if analogue.analogue_count >= 20 and (similarity or 0) >= 0.65 else "LOW",
        confidence=0.7 if analogue.analogue_count >= 20 else 0.5,
        observed_at=_utc(analogue.observed_at),
        reason="Historical analogues are experience/context only and cannot manufacture current BTC direction.",
        context_only=True,
        source=analogue.source,
        metadata={
            "analogue_count": int(analogue.analogue_count),
            "bullish_fraction": bullish_fraction,
            "similarity": similarity,
            "standalone_direction_allowed": False,
            "outcome_blind_at_decision_time": True,
        },
    )


def assemble_btc_perception(
    evidence: list[Evidence],
    *,
    decision_at: datetime,
    trade_horizon: str,
) -> dict:
    """Assemble a BTC-only, instrument-neutral point-in-time market state."""
    state = assemble_market_state(evidence, decision_at=decision_at, trade_horizon=trade_horizon)
    return {
        **state,
        "version": "BTC_SPECIALIST_PERCEPTION_V1",
        "asset": "BTC",
        "default_platform": "COINDCX",
        "global_market_intelligence": True,
        "options_market_is_direction_creator": False,
        "historical_memory_is_direction_creator": False,
        "news_may_contribute_only_after_news_gate": True,
        "onchain_may_contribute_only_after_onchain_gate": True,
        "futures_data_may_inform_options": True,
        "options_and_futures_trade_generation_separate": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_SPECIALIST_PERCEPTION_CONTRACT_V1",
        "asset": "BTC",
        "default_platform": "COINDCX",
        "spot_structure_may_create_direction": True,
        "derivatives_positioning_may_create_direction_conditionally": True,
        "verified_news_may_create_one_event_origin_conditionally": True,
        "verified_onchain_may_create_direction_conditionally": True,
        "coherent_macro_may_create_direction_conditionally": True,
        "options_market_may_create_underlying_direction": False,
        "historical_memory_may_create_current_direction": False,
        "two_independent_causal_origins_required": True,
        "instrument_neutral": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "mixed_instrument_trade_allowed": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }
