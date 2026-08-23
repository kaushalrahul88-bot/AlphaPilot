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
    "draw", "drawdown", "supply cut", "disruption", "outage", "cold", "heatwave",
}
NEGATIVE_WORDS = {
    "miss", "misses", "fall", "falls", "drop", "drops", "decline", "declines", "loss",
    "losses", "downgrade", "cuts", "cut", "probe", "investigation", "penalty", "fraud",
    "weak", "warning", "defaults", "default", "lawsuit", "ban", "slump", "plunge",
    "build", "inventory build", "storage build", "warm weather", "demand concern",
}

COMMODITY_QUERIES = {
    "CRUDEOIL": 'crude oil OR Brent OR WTI OR OPEC OR EIA oil inventory OR Hormuz when:3d',
    "NATURALGAS": 'natural gas OR Henry Hub OR EIA natural gas storage OR LNG OR US weather when:3d',
}

COMMODITY_EVENT_TERMS = {
    "CRUDEOIL": {
        "EIA INVENTORY": ("eia", "inventory"),
        "OPEC": ("opec",),
        "HORMUZ / MIDDLE EAST": ("hormuz", "iran", "middle east"),
        "SUPPLY / OUTAGE": ("supply", "outage", "disruption"),
    },
    "NATURALGAS": {
        "EIA STORAGE": ("eia", "storage"),
        "WEATHER": ("weather", "cold", "heat", "temperature"),
        "LNG": ("lng", "liquefied natural gas"),
        "STORAGE / INVENTORY": ("storage", "inventory"),
    },
}

COMMODITY_BULLISH_PHRASES = {
    "CRUDEOIL": (
        "oil rises", "oil prices rise", "crude rises", "crude prices rise", "brent rises", "wti rises",
        "oil jumps", "oil surges", "crude jumps", "crude surges", "sanctions on iran", "supply disruption",
        "supply disruptions", "output cut", "output cuts", "production cut", "production cuts", "inventory draw",
        "inventories fall", "inventories drop", "hormuz disruption", "shipping disruption",
    ),
    "NATURALGAS": (
        "natural gas futures rise", "natgas futures rise", "natural gas prices rise", "futures leap",
        "futures jump", "futures surge", "output falls", "output drops", "production falls", "production drops",
        "supply disruption", "supply disruptions", "hotter weather boosts", "cold weather boosts",
        "power demand rises", "heating demand rises", "lng exports rise", "storage draw", "inventories fall",
    ),
}

COMMODITY_BEARISH_PHRASES = {
    "CRUDEOIL": (
        "oil falls", "oil prices fall", "crude falls", "crude prices fall", "brent falls", "wti falls",
        "oil drops", "oil slumps", "crude drops", "crude slumps", "output rises", "production rises",
        "inventory build", "inventories rise", "inventories build", "demand falls", "demand declines",
    ),
    "NATURALGAS": (
        "natural gas futures fall", "natgas futures fall", "natural gas prices fall", "futures drop",
        "futures slump", "output rises", "production rises", "record output", "demand falls", "demand declines",
        "storage build", "inventories rise", "warm weather", "warmer weather", "mild weather",
    ),
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


def _commodity_sentiment(symbol: str, headline: str) -> str:
    text = headline.lower()
    bullish = sum(1 for phrase in COMMODITY_BULLISH_PHRASES.get(symbol, ()) if phrase in text)
    bearish = sum(1 for phrase in COMMODITY_BEARISH_PHRASES.get(symbol, ()) if phrase in text)
    if bullish > bearish:
        return "BULLISH"
    if bearish > bullish:
        return "BEARISH"
    return _sentiment(headline)


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


def _event_tags(symbol: str, headline: str) -> list[str]:
    text = headline.lower()
    tags = []
    for label, terms in COMMODITY_EVENT_TERMS.get(symbol, {}).items():
        if any(term in text for term in terms):
            tags.append(label)
    return tags


async def _fetch_google_news(query: str, cache_key: str, limit: int, symbol_for_tags: str | None = None) -> list[dict]:
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1][:limit]

    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    headers = {"User-Agent": "AlphaPilot/1.0 market-news"}
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
        root = ET.fromstring(response.text)
        items: list[dict] = []
        for item in root.findall("./channel/item")[:30]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            published_raw = item.findtext("pubDate")
            source_node = item.find("source")
            source = (source_node.text or "Google News").strip() if source_node is not None else "Google News"
            if not title:
                continue
            row = {
                "headline": title,
                "source": source,
                "published_at": _published_iso(published_raw),
                "url": link,
                "sentiment": _commodity_sentiment(symbol_for_tags, title) if symbol_for_tags else _sentiment(title),
                "preferred_source": _source_rank(source) < len(PREFERRED_SOURCES),
            }
            if symbol_for_tags:
                row["event_tags"] = _event_tags(symbol_for_tags, title)
            items.append(row)

        items.sort(key=lambda row: (_source_rank(row["source"]), -_published_epoch(row.get("published_at"))))
        preferred = [row for row in items if row["preferred_source"]]
        fallback = [row for row in items if not row["preferred_source"]]
        selected = (preferred + fallback)[:limit]
        _cache[cache_key] = (now, selected)
        return selected
    except Exception as exc:
        return [{
            "headline": "News feed temporarily unavailable",
            "source": "AlphaPilot",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "url": None,
            "sentiment": "NEUTRAL",
            "preferred_source": False,
            "event_tags": [],
            "error": str(exc),
        }]


async def _fetch_symbol_news(symbol: str, limit: int = 3) -> list[dict]:
    return await _fetch_google_news(f'"{symbol}" NSE stock when:3d', f"equity:{symbol}", limit)


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


async def latest_commodity_news(symbol: str, limit: int = 4) -> dict:
    normalized = symbol.strip().upper()
    if normalized not in COMMODITY_QUERIES:
        raise ValueError(f"Unsupported commodity news symbol: {normalized}")
    # Version the cache key so sentiment-rule changes do not serve stale classifications.
    rows = await _fetch_google_news(COMMODITY_QUERIES[normalized], f"commodity:v2:{normalized}", max(1, min(limit, 6)), normalized)
    tagged = sorted({tag for row in rows for tag in row.get("event_tags", [])})
    return {
        "provider": "Google News RSS",
        "symbol": normalized,
        "preferred_sources": PREFERRED_SOURCES,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_tags": tagged,
        "items": rows,
    }
