import html
import re
from datetime import datetime, time, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

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

SYMBOL_ALIASES = {
    "RELIANCE": ["reliance industries", "reliance"],
    "TCS": ["tata consultancy services", "tcs"],
    "INFY": ["infosys", "infy"],
    "HDFCBANK": ["hdfc bank", "hdfcbank"],
    "ICICIBANK": ["icici bank", "icicibank"],
    "SBIN": ["state bank of india", "sbi"],
    "AXISBANK": ["axis bank"],
    "KOTAKBANK": ["kotak mahindra bank", "kotak bank"],
    "INDUSINDBK": ["indusind bank"],
    "BAJFINANCE": ["bajaj finance"],
    "BAJAJFINSV": ["bajaj finserv"],
    "LT": ["larsen & toubro", "larsen and toubro"],
    "BHARTIARTL": ["bharti airtel", "airtel"],
    "ITC": ["itc limited", "itc"],
    "HINDUNILVR": ["hindustan unilever", "hul"],
    "MARUTI": ["maruti suzuki", "maruti"],
    "M&M": ["mahindra & mahindra", "mahindra and mahindra"],
    "TATAMOTORS": ["tata motors"],
    "SUNPHARMA": ["sun pharma", "sun pharmaceutical"],
    "DRREDDY": ["dr reddy", "dr. reddy"],
    "CIPLA": ["cipla"],
    "DIVISLAB": ["divi's laboratories", "divis laboratories"],
    "APOLLOHOSP": ["apollo hospitals", "apollo hospital"],
    "WIPRO": ["wipro"],
    "HCLTECH": ["hcl technologies", "hcltech"],
    "TECHM": ["tech mahindra"],
    "LTIM": ["ltimindtree", "lti mindtree"],
    "TITAN": ["titan company", "titan"],
    "ASIANPAINT": ["asian paints"],
    "ULTRACEMCO": ["ultratech cement", "ultratech"],
    "TATASTEEL": ["tata steel"],
    "JSWSTEEL": ["jsw steel"],
    "HINDALCO": ["hindalco"],
    "COALINDIA": ["coal india"],
    "ONGC": ["ongc", "oil and natural gas corporation"],
    "NTPC": ["ntpc"],
    "POWERGRID": ["power grid corporation", "powergrid"],
    "ADANIENT": ["adani enterprises"],
    "ADANIPORTS": ["adani ports"],
    "GRASIM": ["grasim industries", "grasim"],
    "NESTLEIND": ["nestle india"],
    "BRITANNIA": ["britannia industries", "britannia"],
    "EICHERMOT": ["eicher motors"],
    "HEROMOTOCO": ["hero motocorp", "hero moto"],
}


def _clamp(value, low=-10.0, high=10.0):
    return max(low, min(high, value))


def _headline_sentiment(title: str) -> int:
    text = title.lower()
    bull = sum(1 for word in BULLISH_WORDS if word in text)
    bear = sum(1 for word in BEARISH_WORDS if word in text)
    return max(-2, min(2, bull - bear))


def _gift_weight():
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday() >= 5:
        return 1.0, "WEEKEND/NEXT_SESSION"
    if time(9, 15) <= now.time() < time(15, 15):
        return 0.35, "REGULAR_NSE_HOURS"
    return 1.0, "PREOPEN_OR_OVERNIGHT"


def _clean_web_text(raw: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)))


def _parse_gift_derivatives_watch(text: str):
    """Parse nearest GIFT NIFTY future from NSE IX Derivatives Watch text.

    The official page exposes rows in this order:
    Index Futures | NIFTY | expiry | - | - | LTP | change | %change | volume ...
    """
    row = re.search(
        r"Index\s+Futures\s+NIFTY\s+"
        r"(?P<expiry>\d{1,2}-[A-Za-z]{3}-\d{4})\s+"
        r"-\s+-\s+"
        r"(?P<ltp>[0-9,]+(?:\.[0-9]+)?)\s+"
        r"(?P<change>[+-]?[0-9,]+(?:\.[0-9]+)?)\s+"
        r"(?P<pct>[+-]?[0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    if row:
        return {
            "ltp": float(row.group("ltp").replace(",", "")),
            "change": float(row.group("change").replace(",", "")),
            "pct": float(row.group("pct")),
            "expiry": row.group("expiry"),
        }

    marker = re.search(r"Index\s+Futures\s+NIFTY", text, re.IGNORECASE)
    if not marker:
        raise ValueError("NIFTY futures row not found on NSE IX Derivatives Watch")
    window = text[marker.start(): marker.start() + 700]
    expiry = re.search(r"\d{1,2}-[A-Za-z]{3}-\d{4}", window)
    pct_match = re.search(r"([+-]?[0-9]+(?:\.[0-9]+)?)\s*%", window)
    nums = [x.replace(",", "") for x in re.findall(r"[+-]?[0-9]{2,6}(?:,[0-9]{3})*(?:\.[0-9]+)?", window)]
    plausible = [float(x) for x in nums if 5000 <= abs(float(x)) <= 50000]
    if not pct_match or not plausible:
        raise ValueError("GIFT NIFTY row found but quote could not be parsed")
    return {
        "ltp": plausible[0],
        "change": None,
        "pct": float(pct_match.group(1)),
        "expiry": expiry.group(0) if expiry else None,
    }


async def fetch_gift_nifty():
    """Best-effort GIFT NIFTY context from official NSE IX Derivatives Watch."""
    urls = [
        "https://www.nseix.com/markets/derivatives-watch",
        "https://www1.nseix.com/markets/derivatives-watch",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AlphaPilot/0.9; +https://github.com/kaushalrahul88-bot/AlphaPilot)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    last_error = None
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
                response = await client.get(url)
            response.raise_for_status()
            parsed = _parse_gift_derivatives_watch(_clean_web_text(response.text))
            pct = parsed["pct"]
            if pct >= 0.35:
                bias = "BULLISH"
            elif pct <= -0.35:
                bias = "BEARISH"
            else:
                bias = "NEUTRAL"

            raw_score = _clamp(pct * 4.0, -6.0, 6.0)
            weight, regime = _gift_weight()
            return {
                "status": "AVAILABLE",
                "source": "NSE IX Derivatives Watch",
                "source_url": url,
                "ltp": parsed["ltp"],
                "change": parsed["change"],
                "change_pct": round(pct, 2),
                "expiry": parsed["expiry"],
                "bias": bias,
                "raw_context_score": round(raw_score, 1),
                "weight_applied": weight,
                "weight_regime": regime,
                "context_score": round(raw_score * weight, 1),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "warning": "Official public-web context, not an execution-grade licensed market feed.",
            }
        except Exception as exc:
            last_error = exc

    return {
        "status": "UNAVAILABLE",
        "source": "NSE IX Derivatives Watch",
        "bias": "UNKNOWN",
        "context_score": 0.0,
        "error": str(last_error or "Unable to fetch GIFT NIFTY"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _headline_is_relevant(title: str, symbol: str) -> bool:
    text = title.lower()
    aliases = [a.lower() for a in SYMBOL_ALIASES.get(symbol, [])]
    if any(alias in text for alias in aliases):
        return True
    ticker = symbol.lower()
    return len(ticker) >= 5 and re.search(rf"\b{re.escape(ticker)}\b", text) is not None


async def fetch_news_context(symbol: str):
    symbol = symbol.upper().strip()
    aliases = SYMBOL_ALIASES.get(symbol, [symbol])
    primary = aliases[0]
    query = quote_plus(f'"{primary}" stock NSE India when:1d')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0 AlphaPilot/0.9"})
        response.raise_for_status()
        root = ET.fromstring(response.text)
        items = root.findall(".//item")
        headlines = []
        weighted = 0.0
        weight_total = 0.0
        now = datetime.now(timezone.utc)
        discarded = 0

        for item in items:
            if len(headlines) >= 8:
                break
            title = (item.findtext("title") or "").strip()
            if not _headline_is_relevant(title, symbol):
                discarded += 1
                continue
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
            freshness = 1.0 if age_hours is None else max(0.15, 1.0 - min(age_hours, 24.0) / 28.0)
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
        score = round(_clamp(raw * 2.0, -4.0, 4.0), 1)
        bias = "BULLISH" if score >= 1.5 else "BEARISH" if score <= -1.5 else "NEUTRAL"
        return {
            "status": "AVAILABLE" if headlines else "NO_RELEVANT_HEADLINES",
            "source": "Google News RSS",
            "symbol": symbol,
            "query_name": primary,
            "bias": bias,
            "context_score": score,
            "headline_count": len(headlines),
            "discarded_irrelevant": discarded,
            "headlines": headlines,
            "fetched_at": now.isoformat(),
            "warning": "Only stock-name-matched headlines are scored. News remains contextual and cannot create a trade by itself.",
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
