"""Social/narrative intelligence for the research-only Crypto Brain.

Social sources are useful for discovery, narrative velocity and market-impact
awareness, but they are not allowed to manufacture an independent trade vote.
Verified factual claims must be promoted through Crypto News Intelligence where
provenance, corroboration, first-seen time and market confirmation are enforced.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.crypto_market_intelligence import Evidence

Direction = Literal["BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class SocialNarrativeSignal:
    signal_id: str
    event_key: str
    assets: tuple[str, ...]
    platform: str
    first_seen_at: datetime
    source_tier: str
    claim: str
    mention_velocity_percentile: float
    source_historical_reliability: float
    truth_confidence: float
    market_impact_confidence: float
    direction_hint: Direction = "UNKNOWN"
    independent_source_count: int = 1
    primary_source_confirmed: bool = False

    def normalized(self) -> "SocialNarrativeSignal":
        assets = tuple(sorted({str(asset).upper() for asset in self.assets if str(asset).strip()}))
        if not assets:
            raise ValueError("social narrative signal requires at least one affected asset")
        return SocialNarrativeSignal(
            signal_id=str(self.signal_id),
            event_key=str(self.event_key),
            assets=assets,
            platform=str(self.platform).upper(),
            first_seen_at=_utc(self.first_seen_at),
            source_tier=str(self.source_tier).upper(),
            claim=str(self.claim),
            mention_velocity_percentile=_bounded(self.mention_velocity_percentile),
            source_historical_reliability=_bounded(self.source_historical_reliability),
            truth_confidence=_bounded(self.truth_confidence),
            market_impact_confidence=_bounded(self.market_impact_confidence),
            direction_hint=self.direction_hint,
            independent_source_count=max(0, int(self.independent_source_count)),
            primary_source_confirmed=bool(self.primary_source_confirmed),
        )


def social_narrative_context(signal: SocialNarrativeSignal, *, decision_at: datetime) -> Evidence:
    item = signal.normalized()
    decision = _utc(decision_at)
    if item.first_seen_at > decision:
        raise ValueError("social signal was not visible at decision time")

    viral = item.mention_velocity_percentile >= 0.90
    reliable_source = item.source_historical_reliability >= 0.75
    corroborated = item.independent_source_count >= 2 or item.primary_source_confirmed
    high_impact = item.market_impact_confidence >= 0.75

    tags: list[str] = []
    if viral:
        tags.append("VIRAL_NARRATIVE")
    if high_impact:
        tags.append("HIGH_MARKET_IMPACT_POTENTIAL")
    if reliable_source:
        tags.append("HISTORICALLY_RELIABLE_SOURCE")
    if corroborated:
        tags.append("CORROBORATED_DISCOVERY")
    if item.truth_confidence < 0.5:
        tags.append("LOW_TRUTH_CONFIDENCE")

    promotion_candidate = (
        corroborated
        and item.truth_confidence >= 0.80
        and item.direction_hint in {"BULLISH", "BEARISH"}
    )

    reason = (
        "Social narrative is a candidate for factual verification through Crypto News Intelligence; it remains context-only here."
        if promotion_candidate
        else "Social narrative contributes discovery, crowding and market-impact context only; it cannot independently create BTC direction."
    )

    return Evidence(
        family="CRYPTO_SOCIAL_NARRATIVE",
        causal_origin="SOCIAL_NARRATIVE",
        stance="UNKNOWN",
        strength="MEDIUM" if viral or high_impact else "LOW",
        confidence=round(
            0.35
            + 0.20 * item.source_historical_reliability
            + 0.20 * item.truth_confidence
            + 0.25 * item.market_impact_confidence,
            4,
        ),
        observed_at=item.first_seen_at,
        reason=reason,
        context_only=True,
        source=item.platform,
        metadata={
            "signal_id": item.signal_id,
            "event_key": item.event_key,
            "assets": item.assets,
            "source_tier": item.source_tier,
            "claim": item.claim,
            "mention_velocity_percentile": item.mention_velocity_percentile,
            "source_historical_reliability": item.source_historical_reliability,
            "truth_confidence": item.truth_confidence,
            "market_impact_confidence": item.market_impact_confidence,
            "direction_hint": item.direction_hint,
            "independent_source_count": item.independent_source_count,
            "primary_source_confirmed": item.primary_source_confirmed,
            "tags": tags,
            "promotion_candidate_for_news_verification": promotion_candidate,
            "standalone_direction_allowed": False,
            "generates_instrument_trade": False,
        },
    )


def deduplicate_social_signals(signals: list[SocialNarrativeSignal]) -> dict:
    """Group cross-platform repetition by underlying event key, not post count."""
    canonical: dict[str, SocialNarrativeSignal] = {}
    reports: dict[str, int] = {}
    platforms: dict[str, set[str]] = {}
    for raw in signals:
        item = raw.normalized()
        key = item.event_key
        reports[key] = reports.get(key, 0) + 1
        platforms.setdefault(key, set()).add(item.platform)
        current = canonical.get(key)
        if current is None or item.first_seen_at < current.first_seen_at:
            canonical[key] = item
    return {
        "unique_event_count": len(canonical),
        "report_count": len(signals),
        "canonical": [canonical[key].normalized() for key in sorted(canonical)],
        "event_report_counts": dict(sorted(reports.items())),
        "event_platform_counts": {key: len(platforms[key]) for key in sorted(platforms)},
    }


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_SOCIAL_INTELLIGENCE_CONTRACT_V1",
        "social_is_discovery_and_narrative_context": True,
        "viral_equals_true": False,
        "truth_confidence_separate_from_market_impact_confidence": True,
        "standalone_direction_allowed": False,
        "verified_fact_must_pass_news_intelligence": True,
        "duplicate_posts_create_independent_votes": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "broker_execution_enabled": False,
    }
