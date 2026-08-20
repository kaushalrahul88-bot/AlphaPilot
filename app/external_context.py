import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx


BULLISH_WORDS = {
    "beats", "beat", "surge", "surges", "rally", "rallies", "gain", "gains",
    "upgrade", "upgrades", "growth", "record", "approval", "approves",
    "profit", "profits", "strong", "bullish", "buyback", "dividend", "order win",
}
BEARISH_WORDS = {
    "misses", "miss", "falls", "fall", "slump", "drops", "drop", "decline",
    "downgrade", "downgrades", "loss", "losses", "weak", "bearish", "fraud",
    "probe", "penalty", "default", "war", "sanction", "tariff", "lawsuit",
}


def _clamp(value, low=-10.0, high=10.0):
    return max(low, min(high, value))


def _headline_sentiment(title: str) -> int:
    text = title.lower()
    bull = sum(1 for word in BULLISH_WORDS if word in text)
    bear = sum(1 for word in BEARISH_WORDS if word in text)
    return max(-2, min(2, bull - bear))


async def fetch_gift_nifty():
    """Best-effort GIFT NIFTY context from NSE India's public market page.

    This deliberately returns UNAVAILABLE instead of fabricating a quote when
    NSE changes markup or blocks the request.
    """
    url = "https://www.nseindia.com/market-data/live-equity-market"
    headers = {
        "User-Agent": "Mozilla/5.0 AlphaPilot/0.9",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
        response.raise_for_status()
        text = re.sub(r"\s+", " ", response.text)

        # NSE public market page includes text similar to:
        # GiftNiftyFutures 30-Jun-2026 24065.50 198.00 (0.83%)
        pattern = re.compile(
            r"Gift\s*Nifty\s*Futures[^0-9]{0,80}"
            r"(?P<ltp>[0-9]{4,6}(?:\.[0-9]+)?)\s+"
            r"(?P<change>[+-]?[0-9]+(?:\.[0-9]+)?)\s+"
            r"\((?P<pct>[+-]?[0-9]+(?:\.[0-9]+)?)%\)",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if not match:
            # Fallback: find the percentage near the GiftNiftyFutures marker.
            marker = re.search(r"Gift\s*Nifty\s*Futures", text, re.IGNORECASE)
            if not marker:
                raise ValueError("GIFT NIFTY marker not found on NSE page")
            window = text[marker.start(): marker.start() + 500]
            pct_match = re.search(r"\(([+-]?[0-9]+(?:\.[0-9]+)?)%\)", window)
            nums = re.findall(r"\b[0-9]{4,6}(?:\.[0-9]+)?\b", window)
            if not pct_match or not nums:
                raise ValueError("GIFT NIFTY quote could not be parsed")
            pct = float(pct_match.group(1))
            ltp = float(nums[0])
            change = None
        else:
            ltp = float(match.group("ltp"))
            change = float(match.group("change"))
            pct = float(match.group("pct"))

        if pct >= 0.35:
            bias = "BULLISH"
        elif pct <= -0.35:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return {
            "status": "AVAILABLE",
            "source": "NSE India public market page",
            "ltp": ltp,
            "change": change,
            "change_pct": round(pct, 2),
            "bias": bias,
            "context_score": round(_clamp(pct * 4.0, -6.0, 6.0), 1),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "warning": "Best-effort public-page context; not an exchange execution feed.",
        }
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "source": "NSE India public market page",
            "bias": "UNKNOWN",
            "context_score": 0.0,
            "error": str(exc),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }


async def fetch_news_context(symbol: str):
    """Fetch recent Google News RSS headlines and derive a conservative context score."""
    symbol = symbol.upper().strip()
    query = quote_plus(f"{symbol} NSE stock OR India market when:1d")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0 AlphaPilot/0.9"})
        response.raise_for_status()
        root = ET.fromstring(response.text)
        items = root.findall(".//item")[:8]
        headlines = []
        weighted = 0.0
        weight_total = 0.0
        now = datetime.now(timezone.utc)

        for item in items:
            title = (item.findtext("title") or "").strip()
            source = (item.findtext("source") or "").strip()
            pub_raw = (item.findtext("pubDate") or "").strip()
            published = None
            age_hours = None
            try:
                published = parsedate_to_datetime(pub_raw)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                age_hours = max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600)
            except Exception:
                pass

            sentiment = _headline_sentiment(title)
            freshness = 1.0 if age_hours is None else max(0.2, 1.0 - min(age_hours, 24.0) / 30.0)
            weighted += sentiment * freshness
            weight_total += freshness
            headlines.append({
                "title": title,
                "source": source,
                "published_at": published.isoformat() if published else pub_raw or None,
                "age_hours": round(age_hours, 1) if age_hours is not None else None,
                "sentiment": sentiment,
            })

        raw = weighted / weight_total if weight_total else 0.0
        score = round(_clamp(raw * 2.5, -5.0, 5.0), 1)
        bias = "BULLISH" if score >= 1.5 else "BEARISH" if score <= -1.5 else "NEUTRAL"
        return {
            "status": "AVAILABLE" if headlines else "NO_HEADLINES",
            "source": "Google News RSS",
            "symbol": symbol,
            "bias": bias,
            "context_score": score,
            "headline_count": len(headlines),
            "headlines": headlines,
            "fetched_at": now.isoformat(),
            "warning": "Keyword sentiment is contextual and conservative; headlines never create a trade by themselves.",
        }
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "source": "Google News RSS",
            "symbol": symbol,
            "bias": "UNKNOWN",
            "context_score": 0.0,
            "headline_count": 0,
            "headlines": [],
            "error": str(exc),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }


async def external_market_context(symbol: str):
    gift = await fetch_gift_nifty()
    news = await fetch_news_context(symbol)
    return {
        "symbol": symbol.upper(),
        "gift_nifty": gift,
        "news": news,
        "combined_context_adjustment": round(
            _clamp(float(gift.get("context_score", 0)) + float(news.get("context_score", 0)), -8.0, 8.0),
            1,
        ),
        "rule": "Context can confirm or penalize an existing setup; it cannot promote NO_TRADE into SETUP.",
    }
