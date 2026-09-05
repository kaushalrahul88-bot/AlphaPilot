"""Licensed/approved social point-in-time ingestion for the Crypto Brain.

This module intentionally has no Reddit/X/Telegram scraper. A provider may enter
this boundary only after its access, analysis and retention rights are verified
for AlphaPilot's use case. Public visibility alone is not permission to persist
or model user content.

Raw social capture is immutable evidence of what AlphaPilot was allowed to see at
a point in time. It assigns no truth, direction or trade action. Derived event
clustering, narrative velocity and confidence belong in a separately timestamped
enrichment record so later analysis can never rewrite the earlier raw state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Mapping

from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture

SOCIAL_RAW_DATASET = "CRYPTO_SOCIAL_POSTS_AND_NARRATIVE_VELOCITY"
SOCIAL_ENRICHMENT_DATASET = "CRYPTO_SOCIAL_ENRICHMENT"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clean(value: str | None) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class ApprovedSocialCapture:
    provider: str
    platform: str
    post_id: str
    published_at: datetime
    first_seen_at: datetime
    source_url: str
    author_ref: str | None = None
    text: str | None = None
    edited_at: datetime | None = None
    deleted: bool = False
    engagement: Mapping[str, float | int] | None = None
    source_terms_ref: str = ""
    access_approved: bool = False
    analysis_use_approved: bool = False
    retention_approved: bool = False
    source_version: str | None = None

    def validated(self) -> "ApprovedSocialCapture":
        if not _clean(self.provider) or not _clean(self.platform) or not _clean(self.post_id):
            raise ValueError("provider, platform and post_id are required")
        if not _clean(self.source_url):
            raise ValueError("source_url is required")
        if not _clean(self.source_terms_ref):
            raise ValueError("source_terms_ref is required")
        if not self.access_approved or not self.analysis_use_approved or not self.retention_approved:
            raise ValueError("social ingestion requires verified access, analysis-use and retention approval")
        published = _utc(self.published_at)
        first_seen = _utc(self.first_seen_at)
        if first_seen < published:
            raise ValueError("first_seen_at cannot precede published_at")
        if self.edited_at is not None and _utc(self.edited_at) < published:
            raise ValueError("edited_at cannot precede published_at")
        if not self.deleted and not _clean(self.text):
            raise ValueError("non-deleted social capture requires text")
        for key, value in dict(self.engagement or {}).items():
            if not _clean(str(key)):
                raise ValueError("engagement keys must be non-empty")
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("engagement values must be numeric") from exc
            if numeric < 0:
                raise ValueError("engagement values cannot be negative")
        return self


def social_raw_archive_record(capture: ApprovedSocialCapture) -> BtcPitArchiveRecord:
    row = capture.validated()
    published = _utc(row.published_at)
    first_seen = _utc(row.first_seen_at)
    edited = None if row.edited_at is None else _utc(row.edited_at)
    payload = {
        "platform": _clean(row.platform).upper(),
        "post_id": _clean(row.post_id),
        "source_url": _clean(row.source_url),
        "author_ref": None if row.author_ref is None else _clean(row.author_ref),
        "text": None if row.deleted else _clean(row.text),
        "published_at": published.isoformat(),
        "edited_at": None if edited is None else edited.isoformat(),
        "deleted": bool(row.deleted),
        "engagement": dict(row.engagement or {}),
        "source_terms_ref": _clean(row.source_terms_ref),
        "access_approved": True,
        "analysis_use_approved": True,
        "retention_approved": True,
        "truth_assigned": False,
        "direction_assigned": False,
        "virality_equals_truth": False,
        "engagement_equals_market_impact": False,
        "standalone_direction_allowed": False,
        "generates_instrument_trade": False,
    }
    source_key = f"{_clean(row.platform).upper()}:{_clean(row.post_id)}"
    return archive_record_from_capture(
        dataset=SOCIAL_RAW_DATASET,
        provider=_clean(row.provider).upper(),
        source_key=source_key,
        first_seen_at=first_seen,
        event_at=published,
        source_version=row.source_version,
        payload=payload,
    )


@dataclass(frozen=True)
class SocialNarrativeEnrichment:
    raw_natural_key: str
    analysis_first_seen_at: datetime
    event_key: str
    assets: tuple[str, ...]
    claim_summary: str
    mention_velocity_percentile: float
    source_historical_reliability: float
    truth_confidence: float
    market_impact_confidence: float
    direction_hint: str = "UNKNOWN"
    independent_source_count: int = 1
    primary_source_confirmed: bool = False
    analysis_version: str = "SOCIAL_ENRICHMENT_V1"

    def validated(self) -> "SocialNarrativeEnrichment":
        if not _clean(self.raw_natural_key) or not _clean(self.event_key) or not _clean(self.claim_summary):
            raise ValueError("raw_natural_key, event_key and claim_summary are required")
        assets = tuple(sorted({_clean(asset).upper() for asset in self.assets if _clean(asset)}))
        if not assets:
            raise ValueError("social enrichment requires at least one asset")
        for name, value in {
            "mention_velocity_percentile": self.mention_velocity_percentile,
            "source_historical_reliability": self.source_historical_reliability,
            "truth_confidence": self.truth_confidence,
            "market_impact_confidence": self.market_impact_confidence,
        }.items():
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if _clean(self.direction_hint).upper() not in {"BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"}:
            raise ValueError("unsupported direction_hint")
        if int(self.independent_source_count) < 0:
            raise ValueError("independent_source_count cannot be negative")
        return self


def social_enrichment_archive_record(
    raw_record: BtcPitArchiveRecord,
    enrichment: SocialNarrativeEnrichment,
) -> BtcPitArchiveRecord:
    raw = raw_record.validated()
    row = enrichment.validated()
    if raw.dataset != SOCIAL_RAW_DATASET:
        raise ValueError("social enrichment must reference a raw social PIT record")
    raw_frozen = raw.frozen_dict()
    if row.raw_natural_key != raw_frozen["natural_key"]:
        raise ValueError("social enrichment raw_natural_key does not match raw record")
    analysis_seen = _utc(row.analysis_first_seen_at)
    raw_seen = datetime.fromisoformat(raw_frozen["first_seen_at"])
    if analysis_seen < raw_seen:
        raise ValueError("analysis_first_seen_at cannot precede raw social first_seen_at")

    assets = tuple(sorted({_clean(asset).upper() for asset in row.assets if _clean(asset)}))
    payload = {
        "raw_natural_key": row.raw_natural_key,
        "raw_first_seen_at": raw_frozen["first_seen_at"],
        "analysis_first_seen_at": analysis_seen.isoformat(),
        "event_key": _clean(row.event_key),
        "assets": list(assets),
        "claim_summary": _clean(row.claim_summary),
        "mention_velocity_percentile": float(row.mention_velocity_percentile),
        "source_historical_reliability": float(row.source_historical_reliability),
        "truth_confidence": float(row.truth_confidence),
        "market_impact_confidence": float(row.market_impact_confidence),
        "direction_hint": _clean(row.direction_hint).upper(),
        "independent_source_count": int(row.independent_source_count),
        "primary_source_confirmed": bool(row.primary_source_confirmed),
        "standalone_direction_allowed": False,
        "verified_fact_must_pass_news_intelligence": True,
        "generates_instrument_trade": False,
    }
    identity = f"{row.raw_natural_key}|{_clean(row.event_key)}|{analysis_seen.isoformat()}|{row.analysis_version}"
    source_key = sha256(identity.encode("utf-8")).hexdigest()
    return archive_record_from_capture(
        dataset=SOCIAL_ENRICHMENT_DATASET,
        provider="ALPHAPILOT",
        source_key=source_key,
        first_seen_at=analysis_seen,
        event_at=None,
        source_version=row.analysis_version,
        payload=payload,
    )


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_SOCIAL_PIT_INGEST_V1",
        "public_visibility_equals_permission": False,
        "approved_access_required": True,
        "approved_analysis_use_required": True,
        "approved_retention_required": True,
        "scraping_implemented": False,
        "reddit_provider_implemented": False,
        "x_provider_implemented": False,
        "raw_and_enrichment_separate": True,
        "analysis_learned_later_may_be_backdated": False,
        "raw_engagement_equals_truth": False,
        "raw_engagement_equals_market_impact": False,
        "standalone_direction_allowed": False,
        "verified_fact_must_pass_news_intelligence": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
