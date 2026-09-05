import unittest
from datetime import datetime, timezone

from app.newsapi_crypto_provider import (
    EVERYTHING_URL,
    NewsApiCryptoPolicy,
    NewsApiCryptoProvider,
    architecture_contract,
)


def _t():
    return datetime(2026, 9, 5, 6, 30, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, *, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return _Response(self.payload)


class NewsApiCryptoProviderTests(unittest.TestCase):
    def test_disabled_provider_makes_no_network_call(self):
        client = _Client({})
        provider = NewsApiCryptoProvider(client=client)
        with self.assertRaises(RuntimeError):
            provider.capture_latest(first_seen_at=_t())
        self.assertEqual(client.calls, [])

    def test_enabled_provider_requires_api_key(self):
        with self.assertRaises(ValueError):
            NewsApiCryptoPolicy(enabled=True).validated()

    def test_capture_preserves_published_time_and_assigns_first_seen_now(self):
        payload = {
            "status": "ok",
            "totalResults": 1,
            "articles": [{
                "source": {"id": "example", "name": "Example News"},
                "author": "Reporter",
                "title": "Bitcoin market update",
                "description": "A market development.",
                "url": "https://example.com/story#section",
                "publishedAt": "2026-09-05T06:25:00Z",
                "content": "Short API excerpt",
            }],
        }
        client = _Client(payload)
        provider = NewsApiCryptoProvider(NewsApiCryptoPolicy(enabled=True, api_key="secret"), client=client)
        rows = provider.capture_latest(first_seen_at=_t())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.first_seen_at, _t())
        self.assertEqual(row.published_at.isoformat(), "2026-09-05T06:25:00+00:00")
        self.assertEqual(row.canonical_url, "https://example.com/story")
        call = client.calls[0]
        self.assertEqual(call["url"], EVERYTHING_URL)
        self.assertEqual(call["headers"]["X-Api-Key"], "secret")
        self.assertNotIn("apiKey", call["params"])
        self.assertEqual(call["params"]["sortBy"], "publishedAt")

    def test_future_published_article_is_not_admitted(self):
        payload = {
            "status": "ok",
            "articles": [{
                "source": {"name": "Example News"},
                "title": "Future article",
                "url": "https://example.com/future",
                "publishedAt": "2026-09-05T06:31:00Z",
            }],
        }
        provider = NewsApiCryptoProvider(
            NewsApiCryptoPolicy(enabled=True, api_key="secret"),
            client=_Client(payload),
        )
        self.assertEqual(provider.capture_latest(first_seen_at=_t()), [])

    def test_duplicate_url_in_same_poll_is_deduplicated(self):
        article = {
            "source": {"name": "Example News"},
            "title": "Bitcoin update",
            "url": "https://example.com/story",
            "publishedAt": "2026-09-05T06:25:00Z",
        }
        provider = NewsApiCryptoProvider(
            NewsApiCryptoPolicy(enabled=True, api_key="secret"),
            client=_Client({"status": "ok", "articles": [article, dict(article)]}),
        )
        self.assertEqual(len(provider.capture_latest(first_seen_at=_t())), 1)

    def test_contract_keeps_provider_raw_and_non_directional(self):
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertTrue(contract["api_key_required_when_enabled"])
        self.assertTrue(contract["raw_news_capture_only"])
        self.assertFalse(contract["provider_result_is_confirmed_fact"])
        self.assertFalse(contract["provider_result_is_directional_signal"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
