"""Point-in-time news intelligence for the shared Crypto Market Brain.

News is first-class live intelligence, but a headline is never an automatic trade.
Truth confidence and market-impact confidence are deliberately separate: an
unverified rumour may move the market while remaining poor factual evidence.
This module is research/shadow only and emits instrument-neutral Evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Iterable, Literal

from .crypto_market_intelligence import Evidence

Direction = Literal["BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"]
VerificationState = Literal["UNVERIFIED", "PARTIALLY_VERIFIED", "CONFIRMED", "DISPUTED"]
Novelty = Literal["NEW", "UPDATE", "DUPLICATE", "STALE"]
Materiality = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class CryptoNewsItem:
    news_id: str
    event_key: str
    assets: tuple[str, ...]
    headline: str
    published_at: datetime
    first_seen_at: datetime
    source_name: str
    source_class: str
    source_tier: str
    verification_state: VerificationState = "UNVERIFIED"
    truth_confidence: float = 0.5
    market_impact_confidence: float = 0.5
    direction_hint: Direction = "UNKNOWN"
    materiality: Materiality = "UNKNOWN"
    novelty: Novelty = "NEW"
    expected_horizon: str = "intraday"
    primary_source_ref: str | None = None
    claim_summary: str | None = None
    metadata: dict | None = None

    def normalized(self) -> "CryptoNewsItem":
        published_at = _utc(self.published_at)
        first_seen_at = _utc(self.first_seen_at)
        if first_seen_at < published_at:
            raise ValueError("first_seen_at cannot precede published_at")
        assets = tuple(dict.fromkeys(str(asset).upper() for asset in self.assets if str(asset).strip()))
        if not assets:
            assets = ("CRYPTO_MARKET",)
        return replace(
            self,
            assets=assets,
            published_at=published_at,
            first_seen_at=first_seen_at,
            source_class=str(self.source_class or "UNKNOWN").upper(),
            source_tier=str(self.source_tier or "E_UNVERIFIED").upper(),
            truth_confidence=_bounded(self.truth_confidence),
            market_impact_confidence=_bounded(self.market_impact_confidence),
            direction_hint=str(self.direction_hint or "UNKNOWN").upper(),
            materiality=str(self.materiality or "UNKNOWN").upper(),
            novelty=str(self.novelty or "NEW").upper(),
            expected_horizon=str(self.expected_horizon or "intraday").lower(),
        )


def news_replay_eligible(item: CryptoNewsItem, *, decision_at: datetime) -> bool:
    row = item.normalized()
    return row.first_seen_at <= _utc(decision_at)


def deduplicate_news(items: Iterable[CryptoNewsItem]) -> dict:
    """Keep the earliest first-seen item for each event_key and expose later copies.

    Later reports are not thrown away: they are retained as duplicates/updates so
    corroboration and information evolution can be studied without manufacturing
    multiple independent causal votes from the same underlying event.
    """
    canonical: dict[str, CryptoNewsItem] = {}
    related: dict[str, list[CryptoNewsItem]] = {}
    for raw in sorted((item.normalized() for item in items), key=lambda row: row.first_seen_at):
        key = str(raw.event_key or raw.news_id).strip().upper()
        if key not in canonical:
            canonical[key] = raw
            related[key] = []
        else:
            related[key].append(raw)
    return {
        "canonical": [asdict(row) for row in canonical.values()],
        "related_reports": {key: [asdict(row) for row in rows] for key, rows in related.items()},
        "unique_event_count": len(canonical),
        "report_count": len(canonical) + sum(len(rows) for rows in related.values()),
    }


def news_event_context(
    item: CryptoNewsItem,
    *,
    decision_at: datetime,
    independent_corroboration_count: int = 0,
    primary_source_confirmed: bool = False,
    market_confirmation: bool = False,
) -> Evidence:
    """Convert point-in-time news into instrument-neutral market evidence.

    Directional admission is intentionally strict. Professional reporting needs
    corroboration or primary confirmation, and even then the market reaction must
    be consistent before the item can count as one EVENT_INFORMATION origin.
    """
    row = item.normalized()
    if not news_replay_eligible(row, decision_at=decision_at):
        raise ValueError("news item was not first-seen by decision_at")

    source_tier = row.source_tier
    verified = row.verification_state == "CONFIRMED"
    is_primary = source_tier == "A_PRIMARY" or primary_source_confirmed
    community_or_unverified = source_tier in {"D_COMMUNITY", "E_UNVERIFIED"}
    duplicate_or_stale = row.novelty in {"DUPLICATE", "STALE"}
    corroborated = independent_corroboration_count >= 1 or is_primary

    can_directionally_interpret = (
        verified
        and row.truth_confidence >= 0.80
        and row.market_impact_confidence >= 0.60
        and row.materiality in {"MEDIUM", "HIGH"}
        and row.direction_hint in {"BULLISH", "BEARISH"}
        and corroborated
        and market_confirmation
        and not community_or_unverified
        and not duplicate_or_stale
    )

    if can_directionally_interpret:
        stance: Direction = row.direction_hint
        reason = (
            "Confirmed material news with sufficient truth/impact confidence, "
            "corroboration and contemporaneous market confirmation."
        )
        context_only = False
        strength = "HIGH" if row.materiality == "HIGH" and is_primary else "MEDIUM"
    else:
        stance = "UNKNOWN"
        context_only = True
        strength = "LOW"
        if row.verification_state == "DISPUTED":
            reason = "News claim is disputed; retain only as narrative/market-reaction context."
        elif not verified:
            reason = "News claim is not confirmed; truth and market-impact confidence remain separate context variables."
        elif duplicate_or_stale:
            reason = "Duplicate/stale report cannot manufacture a second event-information vote."
        elif community_or_unverified:
            reason = "Community/unverified source cannot become standalone directional evidence."
        elif not corroborated:
            reason = "Material claim lacks independent or primary-source corroboration."
        elif not market_confirmation:
            reason = "Confirmed news lacks contemporaneous market confirmation; direction remains unadmitted."
        else:
            reason = "News does not meet the directional admission contract for this decision."

    confidence = _bounded(row.truth_confidence * 0.6 + row.market_impact_confidence * 0.4)
    return Evidence(
        family="NEWS_EVENT",
        causal_origin="EVENT_INFORMATION",
        stance=stance,
        strength=strength,
        confidence=round(confidence, 4),
        observed_at=row.first_seen_at,
        reason=reason,
        context_only=context_only,
        source=row.source_name,
        metadata={
            "news_id": row.news_id,
            "event_key": row.event_key,
            "assets": list(row.assets),
            "source_class": row.source_class,
            "source_tier": source_tier,
            "verification_state": row.verification_state,
            "truth_confidence": row.truth_confidence,
            "market_impact_confidence": row.market_impact_confidence,
            "materiality": row.materiality,
            "novelty": row.novelty,
            "expected_horizon": row.expected_horizon,
            "published_at": row.published_at.isoformat(),
            "first_seen_at": row.first_seen_at.isoformat(),
            "primary_source_ref": row.primary_source_ref,
            "independent_corroboration_count": independent_corroboration_count,
            "primary_source_confirmed": primary_source_confirmed,
            "market_confirmation": market_confirmation,
            "may_inform_options": True,
            "may_inform_futures": True,
            "generates_instrument_trade": False,
        },
    )


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_NEWS_INTELLIGENCE_V1",
        "news_first_class_live_intelligence": True,
        "published_and_first_seen_timestamps_required": True,
        "historical_replay_uses_first_seen": True,
        "truth_confidence_separate_from_market_impact_confidence": True,
        "headline_is_trade_signal": False,
        "community_news_standalone_direction_vote": False,
        "duplicate_event_can_create_multiple_votes": False,
        "primary_or_independent_corroboration_required": True,
        "market_confirmation_required_for_directional_admission": True,
        "instrument_neutral": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
