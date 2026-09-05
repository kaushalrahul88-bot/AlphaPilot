"""Immutable first-seen archival boundary for raw crypto news discovery."""
from __future__ import annotations

from datetime import datetime, timezone

from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture
from app.crypto_news_intelligence import CryptoNewsItem
from app.newsapi_crypto_provider import NewsApiRawArticleCapture

CRYPTO_NEWS_DATASET = "CRYPTO_NEWS_EVENTS"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def newsapi_article_archive_record(capture: NewsApiRawArticleCapture) -> BtcPitArchiveRecord:
    capture.validated()
    return archive_record_from_capture(
        dataset=CRYPTO_NEWS_DATASET,
        provider=capture.provider,
        source_key=capture.article_key,
        first_seen_at=_utc(capture.first_seen_at),
        event_at=_utc(capture.published_at),
        source_version="NEWSAPI_V2_EVERYTHING_RAW_DISCOVERY_V1",
        payload={
            "article_key": capture.article_key,
            "published_at": _utc(capture.published_at).isoformat(),
            "source_id": capture.source_id,
            "source_name": capture.source_name,
            "author": capture.author,
            "headline": capture.title,
            "description": capture.description,
            "url": capture.canonical_url,
            "content_excerpt": capture.content_excerpt,
            "verification_state": "UNVERIFIED",
            "truth_confidence_assigned": False,
            "market_impact_confidence_assigned": False,
            "direction_assigned": False,
            "materiality_assigned": False,
            "source_reliability_assigned": False,
            "provider_result_treated_as_confirmed_fact": False,
        },
    )


def raw_news_item_from_pit_record(row: dict, *, assets: tuple[str, ...] = ("CRYPTO_MARKET",)) -> CryptoNewsItem:
    if row.get("dataset") != CRYPTO_NEWS_DATASET:
        raise ValueError("PIT record is not a crypto-news record")
    if row.get("first_seen_at") is None or row.get("event_at") is None:
        raise ValueError("news PIT record requires first_seen_at and event_at")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    required = ("article_key", "source_name", "headline", "url")
    if any(not str(payload.get(key) or "").strip() for key in required):
        raise ValueError("news PIT record is missing raw article identity fields")
    first_seen = datetime.fromisoformat(str(row["first_seen_at"]))
    published = datetime.fromisoformat(str(row["event_at"]))
    article_key = str(payload["article_key"])
    return CryptoNewsItem(
        news_id=article_key,
        event_key=article_key,
        assets=assets,
        headline=str(payload["headline"]),
        published_at=published,
        first_seen_at=first_seen,
        source_name=str(payload["source_name"]),
        source_class="RAW_NEWS_DISCOVERY",
        source_tier="E_UNVERIFIED",
        verification_state="UNVERIFIED",
        truth_confidence=0.5,
        market_impact_confidence=0.5,
        direction_hint="UNKNOWN",
        materiality="UNKNOWN",
        novelty="NEW",
        expected_horizon="intraday",
        primary_source_ref=str(payload["url"]),
        claim_summary=None,
        metadata={
            "raw_pit_record": True,
            "provider": row.get("provider"),
            "source_key": row.get("source_key"),
            "source_reliability_assigned": False,
            "requires_news_enrichment_before_directional_admission": True,
        },
    ).normalized()


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_NEWS_PIT_CAPTURE_V1",
        "dataset": CRYPTO_NEWS_DATASET,
        "raw_article_first_seen_is_immutable": True,
        "published_at_separate_from_first_seen_at": True,
        "later_classifier_may_rewrite_raw_capture": False,
        "raw_provider_article_is_confirmed_fact": False,
        "raw_provider_article_has_direction": False,
        "raw_provider_source_name_is_reliability_tier": False,
        "news_intelligence_enrichment_required": True,
        "trade_generation_allowed": False,
        "research_only": True,
    }
