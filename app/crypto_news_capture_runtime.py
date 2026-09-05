"""Environment-gated runtime for raw crypto NewsAPI first-seen capture."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore
from app.crypto_news_capture_scheduler import CryptoNewsCapturePolicy, CryptoNewsPitCaptureScheduler
from app.newsapi_crypto_provider import DEFAULT_QUERY, NewsApiCryptoPolicy, NewsApiCryptoProvider

ENV_ARCHIVE_ENABLED = "ALPHAPILOT_CRYPTO_BTC_PIT_POSTGRES_ENABLED"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_NEWS_ENABLED = "ALPHAPILOT_CRYPTO_NEWSAPI_ENABLED"
ENV_NEWS_API_KEY = "NEWSAPI_API_KEY"
ENV_NEWS_POLL_SECONDS = "ALPHAPILOT_CRYPTO_NEWSAPI_POLL_SECONDS"
ENV_NEWS_QUERY = "ALPHAPILOT_CRYPTO_NEWSAPI_QUERY"
ENV_NEWS_LANGUAGE = "ALPHAPILOT_CRYPTO_NEWSAPI_LANGUAGE"
ENV_NEWS_LOOKBACK_MINUTES = "ALPHAPILOT_CRYPTO_NEWSAPI_LOOKBACK_MINUTES"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


def _int(value: str | None, default: int) -> int:
    if value is None or not str(value).strip():
        return int(default)
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"invalid integer environment value: {value!r}") from exc


@dataclass(frozen=True)
class CryptoNewsCaptureRuntimeConfig:
    archive_enabled: bool = False
    database_url: str = ""
    news_enabled: bool = False
    api_key: str = ""
    poll_seconds: int = 60
    query: str = DEFAULT_QUERY
    language: str = "en"
    lookback_minutes: int = 15

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CryptoNewsCaptureRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            archive_enabled=_bool(source.get(ENV_ARCHIVE_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            news_enabled=_bool(source.get(ENV_NEWS_ENABLED), False),
            api_key=str(source.get(ENV_NEWS_API_KEY, "") or "").strip(),
            poll_seconds=_int(source.get(ENV_NEWS_POLL_SECONDS), 60),
            query=str(source.get(ENV_NEWS_QUERY, DEFAULT_QUERY) or DEFAULT_QUERY).strip(),
            language=str(source.get(ENV_NEWS_LANGUAGE, "en") or "en").strip(),
            lookback_minutes=_int(source.get(ENV_NEWS_LOOKBACK_MINUTES), 15),
        ).validated()

    def validated(self) -> "CryptoNewsCaptureRuntimeConfig":
        CryptoNewsCapturePolicy(enabled=self.news_enabled, poll_seconds=self.poll_seconds).validated()
        NewsApiCryptoPolicy(
            enabled=self.news_enabled,
            api_key=self.api_key,
            query=self.query,
            language=self.language,
            lookback_minutes=self.lookback_minutes,
        ).validated()
        if self.archive_enabled and not self.database_url:
            raise ValueError("crypto PIT archive enabled but DATABASE_URL is missing")
        if self.news_enabled and not self.archive_enabled:
            raise ValueError("crypto news capture cannot run without immutable PIT archive enabled")
        if self.news_enabled and not self.database_url:
            raise ValueError("crypto news capture cannot run without DATABASE_URL")
        return self


def build_crypto_news_capture_runtime(config: CryptoNewsCaptureRuntimeConfig, *, http_client=None) -> dict:
    config = config.validated()
    if not config.archive_enabled:
        return {
            "status": "CRYPTO_NEWS_RUNTIME_DISABLED",
            "config": config,
            "store": None,
            "provider": None,
            "scheduler": None,
        }

    store = PostgresBtcPitArchiveStore(config.database_url)
    provider = NewsApiCryptoProvider(
        NewsApiCryptoPolicy(
            enabled=config.news_enabled,
            api_key=config.api_key,
            query=config.query,
            language=config.language,
            lookback_minutes=config.lookback_minutes,
        ),
        client=http_client,
    )
    scheduler = CryptoNewsPitCaptureScheduler(
        provider=provider,
        store=store,
        policy=CryptoNewsCapturePolicy(enabled=config.news_enabled, poll_seconds=config.poll_seconds),
    )
    return {
        "status": "CRYPTO_NEWS_RUNTIME_READY" if config.news_enabled else "CRYPTO_NEWS_ARCHIVE_ONLY_READY",
        "config": config,
        "store": store,
        "provider": provider,
        "scheduler": scheduler,
    }


async def initialize_crypto_news_capture_runtime(config: CryptoNewsCaptureRuntimeConfig, *, http_client=None) -> dict:
    """Initialize persistence only; never starts news polling."""
    runtime = build_crypto_news_capture_runtime(config, http_client=http_client)
    if runtime["store"] is None:
        return {"status": "CRYPTO_NEWS_RUNTIME_DISABLED", "schema_initialized": False, "capture_started": False}
    initialized = await runtime["store"].initialize()
    return {
        "status": runtime["status"],
        "schema_initialized": initialized["status"] == "BTC_PIT_POSTGRES_SCHEMA_READY",
        "capture_started": False,
        "scheduler_enabled": runtime["scheduler"].policy.enabled,
    }


async def run_crypto_news_capture_service(config: CryptoNewsCaptureRuntimeConfig, *, stop_event, http_client=None) -> dict:
    """Explicit service entrypoint; caller must deliberately invoke it."""
    runtime = build_crypto_news_capture_runtime(config, http_client=http_client)
    if runtime["scheduler"] is None:
        return {"status": "CRYPTO_NEWS_RUNTIME_DISABLED", "cycles": 0}
    await runtime["store"].initialize()
    return await runtime["scheduler"].run_until_stopped(stop_event)


def runtime_status(config: CryptoNewsCaptureRuntimeConfig) -> dict:
    config = config.validated()
    return {
        "version": "CRYPTO_NEWS_CAPTURE_RUNTIME_STATUS_V1",
        "archive_enabled": config.archive_enabled,
        "news_enabled": config.news_enabled,
        "database_configured": bool(config.database_url),
        "api_key_configured": bool(config.api_key),
        "poll_seconds": config.poll_seconds,
        "query": config.query,
        "language": config.language,
        "lookback_minutes": config.lookback_minutes,
        "automatic_startup_registration": False,
        "network_request_performed": False,
        "trade_generation_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_NEWS_CAPTURE_RUNTIME_V1",
        "news_enabled_by_default": False,
        "api_key_required_when_enabled": True,
        "archive_required_before_news_capture": True,
        "database_required_before_news_capture": True,
        "btc_derivatives_capture_enables_news": False,
        "automatic_startup_registration": False,
        "automatic_network_request": False,
        "feed_assigns_truth_or_direction": False,
        "trade_generation_enabled": False,
        "research_only": True,
    }
