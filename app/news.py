import asyncio
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import httpx

PREFERRED_SOURCES = [
    "Reuters",
    "CNBC-TV18",
    "Moneycontrol",
    "The Economic Times",
    "Economic Times",
    "Business Standard",
    "Mint",
]

SOURCE_RANK = {name.lower(): index for index, name in enumerate(PREFERRED_SOURCES)}
CACHE_TTL_SECONDS = 15 * 60
_cache: dict[str, tuple[float, list[dict]]] = {}

POSITIVE_WORDS = {
    "beat", "beats", "surge", "surges", "rise", "rises", "gain", "gains", "growth",
    "upgrade", "upgrades", "record", "profit", "profits", "strong", "wins", "win",
    "approval", "approved", "expands", "expansion", "dividend", "buyback", "order win",
}
NEGATIVE_WORDS = {
    "miss", "misses", "fall", "falls", "drop", "drops", "decline", "declines", "loss",
    "losses", "downgrade", "cuts", "cut", "probe", "investigation", "penalty", "fraud",
    "weak", "warning", "defaults", "default", "lawsuit", "ban", "slump", "plunge",
}


def _sentiment(headline: str) -> str:
    text = headline.lower()
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    if positive > negative:
        return "BULLISH"
    if negative > positive:
        return "BEARISH"
    return "NEUTRAL"


def _published_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _published_epoch(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _source_rank(source: str) -> int:
    key = source.lower().strip()
    for preferred, rank in SOURCE_RANK.items():
        if preferred in key or key in preferred:
            return rank
    return len(PREFERRED_SOURCES) + 10


async def _fetch_symbol_news(symbol: str, limit: int = 3) -> list[dict]:
    now = time.time()
    cached = _cache.get(symbol)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1][:limit]

    query = quote_plus(f'"{symbol}" NSE stock when:3d')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    headers = {"User-Agent": "AlphaPilot/1.0 market-news"}

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
        root = ET.fromstring(response.text)
        items: list[dict] = []
        for item in root.findall("./channel/item")[:20]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            published_raw = item.findtext("pubDate")
            source_node = item.find("source")
            source = (source_node.text or "Google News").strip() if source_node is not None else "Google News"
            if not title:
                continue
            items.append({
                "headline": title,
                "source": source,
                "published_at": _published_iso(published_raw),
                "url": link,
                "sentiment": _sentiment(title),
                "preferred_source": _source_rank(source) < len(PREFERRED_SOURCES),
            })

        items.sort(key=lambda row: (_source_rank(row["source"]), -_published_epoch(row.get("published_at"))))
        preferred = [row for row in items if row["preferred_source"]]
        fallback = [row for row in items if not row["preferred_source"]]
        selected = (preferred + fallback)[:limit]
        _cache[symbol] = (now, selected)
        return selected
    except Exception as exc:
        return [{
            "headline": "News feed temporarily unavailable",
            "source": "AlphaPilot",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "url": None,
            "sentiment": "NEUTRAL",
            "preferred_source": False,
            "error": str(exc),
        }]


async def latest_market_news(symbols: list[str], limit: int = 3) -> dict:
    cleaned: list[str] = []
    for symbol in symbols:
        normalized = symbol.strip().upper()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    cleaned = cleaned[:20]

    semaphore = asyncio.Semaphore(4)

    async def guarded(symbol: str):
        async with semaphore:
            return symbol, await _fetch_symbol_news(symbol, limit)

    pairs = await asyncio.gather(*(guarded(symbol) for symbol in cleaned))
    return {
        "provider": "Google News RSS",
        "preferred_sources": PREFERRED_SOURCES,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": {symbol: rows for symbol, rows in pairs},
    }
