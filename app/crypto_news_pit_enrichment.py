"""Versioned point-in-time enrichment for immutable raw crypto news capture.

Raw headlines are never rewritten. Verification, source tier, event grouping,
truth/impact confidence, materiality and direction are stored as a separate
derived record with its own analysis_first_seen_at so later verification can
never leak into an earlier replay click.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite

from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture
from app.crypto_news_intelligence import CryptoNewsItem, news_event_context

CRYPTO_NEWS_ENRICHMENT_DATASET = "CRYPTO_NEWS_ENRICHMENT"
_VALID_VERIFICATION = {"UNVERIFIED", "PARTIALLY_VERIFIED", "CONFIRMED", "DISPUTED"}
_VALID_DIRECTION = {"BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"}
_VALID_MATERIALITY = {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}
_VALID_NOVELTY = {"NEW", "UPDATE", "DUPLICATE", "STALE"}


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bounded(name: str, value: float) -> float:
    number = float(value)
    if not isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be finite and within 0..1")
    return number


@dataclass(frozen=True)
class CryptoNewsEnrichmentCapture:
    event_key: str
    assets: tuple[str, ...]
    headline: str
    published_at: datetime
    raw_first_seen_at: datetime
    analysis_first_seen_at: datetime
    source_name: str
    source_class: str
    source_tier: str
    verification_state: str
    truth_confidence: float
    market_impact_confidence: float
    direction_hint: str
    materiality: str
    novelty: str
    expected_horizon: str
    input_news_ids: tuple[str, ...]
    input_source_keys: tuple[str, ...]
    analysis_method: str
    analysis_version: str
    independent_corroboration_count: int = 0
    primary_source_confirmed: bool = False
    primary_source_ref: str | None = None
    claim_summary: str | None = None

    def validated(self) -> "CryptoNewsEnrichmentCapture":
        published = _utc(self.published_at)
        raw_seen = _utc(self.raw_first_seen_at)
        analysis_seen = _utc(self.analysis_first_seen_at)
        if published > raw_seen:
            raise ValueError("published_at cannot be after raw_first_seen_at")
        if raw_seen > analysis_seen:
            raise ValueError("analysis_first_seen_at cannot precede raw_first_seen_at")
        if not str(self.event_key or "").strip():
            raise ValueError("event_key is required")
        if not str(self.headline or "").strip() or not str(self.source_name or "").strip():
            raise ValueError("headline and source_name are required")
        if not str(self.source_class or "").strip() or not str(self.source_tier or "").strip():
            raise ValueError("source_class and source_tier are required")
        if str(self.verification_state).upper() not in _VALID_VERIFICATION:
            raise ValueError("unsupported verification_state")
        if str(self.direction_hint).upper() not in _VALID_DIRECTION:
            raise ValueError("unsupported direction_hint")
        if str(self.materiality).upper() not in _VALID_MATERIALITY:
            raise ValueError("unsupported materiality")
        if str(self.novelty).upper() not in _VALID_NOVELTY:
            raise ValueError("unsupported novelty")
        _bounded("truth_confidence", self.truth_confidence)
        _bounded("market_impact_confidence", self.market_impact_confidence)
        if int(self.independent_corroboration_count) < 0:
            raise ValueError("independent_corroboration_count must be >= 0")
        if not tuple(value for value in self.input_news_ids if str(value).strip()):
            raise ValueError("at least one input_news_id is required")
        if not tuple(value for value in self.input_source_keys if str(value).strip()):
            raise ValueError("at least one input_source_key is required")
        if not str(self.analysis_method or "").strip() or not str(self.analysis_version or "").strip():
            raise ValueError("analysis_method and analysis_version are required")
        return self

    @property
    def source_key(self) -> str:
        self.validated()
        identity = "|".join((
            str(self.event_key).strip().upper(),
            str(self.analysis_method).strip().upper(),
            str(self.analysis_version).strip(),
            _utc(self.analysis_first_seen_at).isoformat(),
        ))
        return sha256(identity.encode("utf-8")).hexdigest()


def news_enrichment_archive_record(capture: CryptoNewsEnrichmentCapture) -> BtcPitArchiveRecord:
    capture.validated()
    return archive_record_from_capture(
        dataset=CRYPTO_NEWS_ENRICHMENT_DATASET,
        provider="ALPHAPILOT",
        source_key=capture.source_key,
        first_seen_at=_utc(capture.analysis_first_seen_at),
        event_at=_utc(capture.published_at),
        source_version=str(capture.analysis_version),
        payload={
            "event_key": str(capture.event_key).strip().upper(),
            "assets": list(dict.fromkeys(str(asset).upper() for asset in capture.assets if str(asset).strip())) or ["CRYPTO_MARKET"],
            "headline": capture.headline,
            "published_at": _utc(capture.published_at).isoformat(),
            "raw_first_seen_at": _utc(capture.raw_first_seen_at).isoformat(),
            "analysis_first_seen_at": _utc(capture.analysis_first_seen_at).isoformat(),
            "source_name": capture.source_name,
            "source_class": str(capture.source_class).upper(),
            "source_tier": str(capture.source_tier).upper(),
            "verification_state": str(capture.verification_state).upper(),
            "truth_confidence": _bounded("truth_confidence", capture.truth_confidence),
            "market_impact_confidence": _bounded("market_impact_confidence", capture.market_impact_confidence),
            "direction_hint": str(capture.direction_hint).upper(),
            "materiality": str(capture.materiality).upper(),
            "novelty": str(capture.novelty).upper(),
            "expected_horizon": str(capture.expected_horizon or "intraday").lower(),
            "input_news_ids": list(capture.input_news_ids),
            "input_source_keys": list(capture.input_source_keys),
            "analysis_method": capture.analysis_method,
            "analysis_version": capture.analysis_version,
            "independent_corroboration_count": int(capture.independent_corroboration_count),
            "primary_source_confirmed": bool(capture.primary_source_confirmed),
            "primary_source_ref": capture.primary_source_ref,
            "claim_summary": capture.claim_summary,
            "raw_capture_rewritten": False,
            "trade_generated": False,
        },
    )


def enriched_news_item_from_pit_record(row: dict) -> CryptoNewsItem:
    if row.get("dataset") != CRYPTO_NEWS_ENRICHMENT_DATASET:
        raise ValueError("PIT record is not a crypto-news enrichment record")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    if row.get("first_seen_at") is None:
        raise ValueError("news enrichment PIT record requires first_seen_at")
    published = datetime.fromisoformat(str(payload.get("published_at")))
    analysis_seen = datetime.fromisoformat(str(row["first_seen_at"]))
    input_ids = tuple(str(value) for value in payload.get("input_news_ids", []) if str(value).strip())
    news_id = input_ids[0] if input_ids else str(row.get("source_key") or "ENRICHED_NEWS")
    return CryptoNewsItem(
        news_id=news_id,
        event_key=str(payload.get("event_key") or news_id),
        assets=tuple(payload.get("assets") or ("CRYPTO_MARKET",)),
        headline=str(payload.get("headline") or ""),
        published_at=published,
        first_seen_at=analysis_seen,
        source_name=str(payload.get("source_name") or "UNKNOWN_SOURCE"),
        source_class=str(payload.get("source_class") or "UNKNOWN"),
        source_tier=str(payload.get("source_tier") or "E_UNVERIFIED"),
        verification_state=str(payload.get("verification_state") or "UNVERIFIED"),
        truth_confidence=float(payload.get("truth_confidence", 0.5)),
        market_impact_confidence=float(payload.get("market_impact_confidence", 0.5)),
        direction_hint=str(payload.get("direction_hint") or "UNKNOWN"),
        materiality=str(payload.get("materiality") or "UNKNOWN"),
        novelty=str(payload.get("novelty") or "NEW"),
        expected_horizon=str(payload.get("expected_horizon") or "intraday"),
        primary_source_ref=payload.get("primary_source_ref"),
        claim_summary=payload.get("claim_summary"),
        metadata={
            "raw_first_seen_at": payload.get("raw_first_seen_at"),
            "analysis_first_seen_at": payload.get("analysis_first_seen_at"),
            "input_news_ids": list(input_ids),
            "input_source_keys": list(payload.get("input_source_keys") or []),
            "analysis_method": payload.get("analysis_method"),
            "analysis_version": payload.get("analysis_version"),
            "independent_corroboration_count": int(payload.get("independent_corroboration_count", 0)),
            "primary_source_confirmed": bool(payload.get("primary_source_confirmed", False)),
            "raw_capture_rewritten": False,
        },
    ).normalized()


def enriched_news_evidence_from_pit_record(row: dict, *, decision_at: datetime, market_confirmation: bool):
    item = enriched_news_item_from_pit_record(row)
    metadata = item.metadata or {}
    return news_event_context(
        item,
        decision_at=decision_at,
        independent_corroboration_count=int(metadata.get("independent_corroboration_count", 0)),
        primary_source_confirmed=bool(metadata.get("primary_source_confirmed", False)),
        market_confirmation=market_confirmation,
    )


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_NEWS_PIT_ENRICHMENT_V1",
        "raw_capture_rewritten": False,
        "analysis_has_separate_first_seen": True,
        "verification_can_be_backdated_to_raw_first_seen": False,
        "later_analysis_creates_new_versioned_record": True,
        "market_confirmation_still_required_at_decision": True,
        "enrichment_itself_generates_trade": False,
        "research_only": True,
    }
