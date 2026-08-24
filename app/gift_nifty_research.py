from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")
SOURCES = (
    ("NSE IX derivatives watch", "https://www.nseix.com/markets/derivatives-watch"),
    ("NSE IX derivatives watch mirror", "https://www1.nseix.com/markets/derivatives-watch"),
    ("NSE IX home", "https://www.nseix.com/"),
    ("NSE IX home mirror", "https://www1.nseix.com/"),
)


def _clean(raw: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)))


def _parse(text: str) -> tuple[float, float, float, str]:
    cleaned = _clean(text)
    patterns = (
        r"Index\s+Futures\s+NIFTY\s+(?P<expiry>\d{1,2}-[A-Za-z]{3}-\d{4})\s+-\s+-\s+(?P<ltp>[0-9,]+(?:\.[0-9]+)?)\s+(?P<change>[+-]?[0-9,]+(?:\.[0-9]+)?)\s+(?P<pct>[+-]?[0-9]+(?:\.[0-9]+)?)",
        r"Index\s+Futures\s+NIFTY\s+(?P<expiry>\d{1,2}-[A-Za-z]{3}-\d{4}).{0,80}?(?P<ltp>2[0-9,]{3,}(?:\.[0-9]+)?)\s+(?P<change>[+-]?[0-9,]+(?:\.[0-9]+)?)\s+(?P<pct>[+-]?[0-9]+(?:\.[0-9]+)?)",
    )
    matches = []
    for pattern in patterns:
        for m in re.finditer(pattern, cleaned, re.I):
            try:
                expiry = datetime.strptime(m.group("expiry"), "%d-%b-%Y").date()
                ltp = float(m.group("ltp").replace(",", ""))
                change = float(m.group("change").replace(",", ""))
                pct = float(m.group("pct"))
                if 5000 <= ltp <= 50000 and abs(pct) <= 10:
                    matches.append((expiry, ltp, change, pct))
            except Exception:
                continue
        if matches:
            break
    if not matches:
        raise ValueError("NIFTY futures row not parseable")
    today = datetime.now(IST).date()
    future = [x for x in matches if x[0] >= today]
    chosen = min(future or matches, key=lambda x: abs((x[0] - today).days))
    expiry, ltp, change, pct = chosen
    return ltp, change, pct, expiry.strftime("%d-%b-%Y")


def _bias(pct: float) -> str:
    if pct >= 0.35:
        return "BULLISH"
    if pct <= -0.35:
        return "BEARISH"
    return "NEUTRAL"


async def gift_nifty_research() -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    attempts = []
    async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
        for source, url in SOURCES:
            try:
                response = await client.get(url)
                response.raise_for_status()
                ltp, change, pct, expiry = _parse(response.text)
                return {
                    "status": "AVAILABLE",
                    "source": source,
                    "source_url": url,
                    "ltp": round(ltp, 2),
                    "change": round(change, 2),
                    "change_pct": round(pct, 2),
                    "expiry": expiry,
                    "bias": _bias(pct),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "research_only": True,
                    "production_rules_changed": False,
                    "attempts": attempts,
                    "warning": "Official NSE IX public-page research context only; not execution-grade.",
                }
            except Exception as exc:
                attempts.append({"source": source, "url": url, "error": str(exc) or exc.__class__.__name__})
    return {
        "status": "UNAVAILABLE",
        "source": "NSE IX official public pages",
        "bias": "UNKNOWN",
        "attempts": attempts,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "production_rules_changed": False,
        "warning": "Official GIFT NIFTY public pages were unreachable or unparseable; no proxy was fabricated.",
    }
