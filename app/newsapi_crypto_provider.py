"""Optional NewsAPI provider for first-seen Crypto Brain news capture.

The provider is disabled by default and requires an explicit API key. It captures
raw article discovery state only; source reliability, truth confidence,
materiality and direction remain downstream intelligence judgments.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

EVERYTHING_URL = "https://newsapi.org/v2/everything"
DEFAULT_QUERY = "bitcoin OR ethereum OR solana OR crypto OR cryptocurrency OR stablecoin OR blockchain OR defi"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _published(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("NewsAPI publishedAt is required")
    stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return _utc(stamp)


def _canonical_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("news article URL is required")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("news article URL must be http(s)")
    # Fragments never identify a distinct article. Preserve query parameters
    # because some publishers use them as real article identifiers.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


@dataclass(frozen=True)
class NewsApiCryptoPolicy:
    enabled: bool = False
    api_key: str = ""
    timeout_seconds: float = 10.0
    query: str = DEFAULT_QUERY
    language: str = "en"
    lookback_minutes: int = 15
    page_size: int = 100

    def validated(self) -> "NewsApiCryptoPolicy":
        if self.enabled and not str(self.api_key or "").strip():
            raise ValueError("NewsAPI capture enabled but API key is missing")
        if not str(self.query or "").strip() or len(self.query) > 500:
            raise ValueError("NewsAPI query must be 1..500 characters")
        if not str(self.language or "").strip():
            raise ValueError("NewsAPI language is required")
        if not 1 <= int(self.lookback_minutes) <= 24 * 60:
            raise ValueError("NewsAPI lookback_minutes must be 1..1440")
        if not 1 <= int(self.page_size) <= 100:
            raise ValueError("NewsAPI page_size must be 1..100")
        if float(self.timeout_seconds) <= 0:
            raise ValueError("NewsAPI timeout_seconds must be > 0")
        return self


@dataclass(frozen=True)
class NewsApiRawArticleCapture:
    first_seen_at: datetime
    published_at: datetime
    source_id: str | None
    source_name: str
    author: str | None
    title: str
    description: str | None
    url: str
    content_excerpt: str | None
    provider: str = "NEWSAPI_V2"

    def validated(self) -> "NewsApiRawArticleCapture":
        first_seen = _utc(self.first_seen_at)
        published = _utc(self.published_at)
        if published > first_seen:
            raise ValueError("published_at cannot be after first_seen_at")
        if not str(self.source_name or "").strip():
            raise ValueError("source_name is required")
        if not str(self.title or "").strip():
            raise ValueError("title is required")
        _canonical_url(self.url)
        return self

    @property
    def canonical_url(self) -> str:
        self.validated()
        return _canonical_url(self.url)

    @property
    def article_key(self) -> str:
        return sha256(self.canonical_url.encode("utf-8")).hexdigest()


class NewsApiCryptoProvider:
    def __init__(self, policy: NewsApiCryptoPolicy | None = None, client: httpx.Client | None = None):
        self.policy = (policy or NewsApiCryptoPolicy()).validated()
        self._client = client

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("NewsAPI crypto collection is disabled by policy")

    def capture_latest(self, *, first_seen_at: datetime) -> list[NewsApiRawArticleCapture]:
        self._require_enabled()
        first_seen = _utc(first_seen_at)
        start = first_seen - timedelta(minutes=self.policy.lookback_minutes)
        params = {
            "q": self.policy.query,
            "searchIn": "title,description",
            "from": start.isoformat(),
            "to": first_seen.isoformat(),
            "language": self.policy.language,
            "sortBy": "publishedAt",
            "pageSize": int(self.policy.page_size),
            "page": 1,
        }
        headers = {"X-Api-Key": self.policy.api_key}
        if self._client is not None:
            response = self._client.get(EVERYTHING_URL, params=params, headers=headers, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(EVERYTHING_URL, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "ok" or not isinstance(payload.get("articles"), list):
            raise ValueError("invalid NewsAPI response payload")

        captures: dict[str, NewsApiRawArticleCapture] = {}
        for raw in payload["articles"]:
            if not isinstance(raw, dict):
                continue
            published = _published(raw.get("publishedAt"))
            if published > first_seen:
                continue
            source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
            capture = NewsApiRawArticleCapture(
                first_seen_at=first_seen,
                published_at=published,
                source_id=None if source.get("id") is None else str(source.get("id")),
                source_name=str(source.get("name") or "UNKNOWN_SOURCE"),
                author=None if raw.get("author") is None else str(raw.get("author")),
                title=str(raw.get("title") or "").strip(),
                description=None if raw.get("description") is None else str(raw.get("description")),
                url=str(raw.get("url") or ""),
                content_excerpt=None if raw.get("content") is None else str(raw.get("content")),
            ).validated()
            existing = captures.get(capture.article_key)
            if existing is not None and existing != capture:
                raise ValueError("conflicting duplicate NewsAPI article in one capture")
            captures[capture.article_key] = capture
        return sorted(captures.values(), key=lambda row: (row.published_at, row.article_key))


def architecture_contract() -> dict:
    return {
        "version": "NEWSAPI_CRYPTO_PROVIDER_V1",
        "provider": "NEWSAPI_V2",
        "collection_enabled_by_default": False,
        "api_key_required_when_enabled": True,
        "raw_news_capture_only": True,
        "published_at_from_provider": True,
        "first_seen_at_from_alphapilot": True,
        "provider_source_name_is_source_reliability_score": False,
        "provider_result_is_confirmed_fact": False,
        "provider_result_is_directional_signal": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
