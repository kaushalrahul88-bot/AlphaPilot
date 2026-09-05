"""Disabled-by-default scheduler for first-seen crypto news archival."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.crypto_news_pit_capture import CRYPTO_NEWS_DATASET, newsapi_article_archive_record
from app.newsapi_crypto_provider import NewsApiCryptoProvider


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _insert(store: Any, record) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("news PIT store insert_first_seen must return a dict")
    return result


@dataclass(frozen=True)
class CryptoNewsCapturePolicy:
    enabled: bool = False
    poll_seconds: int = 60
    continue_after_article_failure: bool = True

    def validated(self) -> "CryptoNewsCapturePolicy":
        if int(self.poll_seconds) < 30:
            raise ValueError("crypto news poll_seconds must be >= 30")
        return self


class CryptoNewsPitCaptureScheduler:
    def __init__(self, *, provider: NewsApiCryptoProvider, store: Any, policy: CryptoNewsCapturePolicy | None = None):
        self.provider = provider
        self.store = store
        self.policy = (policy or CryptoNewsCapturePolicy()).validated()
        self.cycles = 0
        self.inserted_records = 0
        self.idempotent_duplicates = 0
        self.failures = 0

    async def run_cycle(self, *, now: datetime | None = None) -> dict:
        stamp = _utc(now or datetime.now(timezone.utc))
        if not self.policy.enabled:
            return {
                "status": "CRYPTO_NEWS_CAPTURE_DISABLED",
                "provider_called": False,
                "store_written": False,
                "captured": [],
                "errors": [],
                "trade_generated": False,
            }

        self.cycles += 1
        try:
            articles = await asyncio.to_thread(self.provider.capture_latest, first_seen_at=stamp)
        except Exception as exc:
            self.failures += 1
            return {
                "status": "CRYPTO_NEWS_CAPTURE_PROVIDER_FAILURE",
                "provider_called": True,
                "store_written": False,
                "captured": [],
                "errors": [{"error_type": exc.__class__.__name__, "message": str(exc)}],
                "trade_generated": False,
            }

        captured = []
        errors = []
        for article in articles:
            record = newsapi_article_archive_record(article)
            try:
                stored = await _insert(self.store, record)
                status = stored.get("status")
                if status == "INSERTED_FIRST_SEEN":
                    self.inserted_records += 1
                elif status == "IDEMPOTENT_DUPLICATE":
                    self.idempotent_duplicates += 1
                captured.append({
                    "dataset": CRYPTO_NEWS_DATASET,
                    "source_key": record.source_key,
                    "source_name": article.source_name,
                    "headline": article.title,
                    "published_at": _utc(article.published_at).isoformat(),
                    "first_seen_at": _utc(article.first_seen_at).isoformat(),
                    "storage_status": status,
                    "direction_assigned": False,
                    "truth_confidence_assigned": False,
                    "trade_generated": False,
                })
            except Exception as exc:
                self.failures += 1
                errors.append({
                    "source_key": record.source_key,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                    "first_seen_record_preserved": True,
                })
                if not self.policy.continue_after_article_failure:
                    break

        return {
            "status": "CRYPTO_NEWS_CAPTURE_CYCLE_COMPLETE" if not errors else "CRYPTO_NEWS_CAPTURE_CYCLE_PARTIAL_FAILURE",
            "provider_called": True,
            "store_written": any(row["storage_status"] == "INSERTED_FIRST_SEEN" for row in captured),
            "captured": captured,
            "errors": errors,
            "state": {
                "cycles": self.cycles,
                "inserted_records": self.inserted_records,
                "idempotent_duplicates": self.idempotent_duplicates,
                "failures": self.failures,
            },
            "trade_generated": False,
        }

    async def run_until_stopped(self, stop_event: asyncio.Event) -> dict:
        if not self.policy.enabled:
            return {"status": "CRYPTO_NEWS_CAPTURE_DISABLED", "cycles": 0}
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.policy.poll_seconds)
            except TimeoutError:
                pass
        return {"status": "CRYPTO_NEWS_CAPTURE_STOPPED", "cycles": self.cycles}


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_NEWS_CAPTURE_SCHEDULER_V1",
        "collection_enabled_by_default": False,
        "scheduler_starts_at_import": False,
        "minimum_poll_seconds": 30,
        "raw_capture_only": True,
        "provider_failure_becomes_trade_signal": False,
        "article_conflict_overwrites_first_seen": False,
        "news_feed_assigns_direction": False,
        "news_feed_assigns_truth_confidence": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
