from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

ASSETS = {
    "INDIA_VIX": {"ticker": "^INDIAVIX", "label": "India VIX", "direction": "risk_inverse"},
    "USDINR": {"ticker": "INR=X", "label": "USD/INR", "direction": "risk_inverse"},
    "DXY": {"ticker": "DX-Y.NYB", "label": "US Dollar Index", "direction": "risk_inverse"},
    "US_10Y": {"ticker": "^TNX", "label": "US 10Y yield", "direction": "risk_inverse"},
    "BRENT": {"ticker": "BZ=F", "label": "Brent crude", "direction": "context"},
    "NASDAQ_FUTURES": {"ticker": "NQ=F", "label": "Nasdaq futures", "direction": "risk_positive"},
}


def _bias(change_pct: float | None, direction: str) -> str:
    if change_pct is None or abs(change_pct) < 0.15:
        return "NEUTRAL"
    up = change_pct > 0
    if direction == "risk_positive":
        return "RISK_ON" if up else "RISK_OFF"
    if direction == "risk_inverse":
        return "RISK_OFF" if up else "RISK_ON"
    return "UP" if up else "DOWN"


async def _fetch_one(client: httpx.AsyncClient, key: str, spec: dict) -> tuple[str, dict]:
    ticker = spec["ticker"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}?range=2d&interval=5m&includePrePost=true"
    try:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 AlphaPilot/1.0"})
        r.raise_for_status()
        result = (r.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            raise ValueError("empty chart result")
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None:
            closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            price = next((x for x in reversed(closes) if isinstance(x, (int, float))), None)
        change_pct = ((float(price) / float(previous)) - 1) * 100 if price is not None and previous not in (None, 0) else None
        return key, {
            "status": "AVAILABLE",
            "label": spec["label"],
            "ticker": ticker,
            "value": round(float(price), 4) if price is not None else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "bias": _bias(change_pct, spec["direction"]),
            "source": "Yahoo Finance public chart",
            "exchange_timezone": meta.get("exchangeTimezoneName"),
            "market_state": meta.get("marketState"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True,
        }
    except Exception as exc:
        return key, {
            "status": "UNAVAILABLE",
            "label": spec["label"],
            "ticker": ticker,
            "bias": "UNKNOWN",
            "source": "Yahoo Finance public chart",
            "error": str(exc) or exc.__class__.__name__,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True,
        }


async def cross_asset_snapshot() -> dict:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        pairs = await asyncio.gather(*[_fetch_one(client, key, spec) for key, spec in ASSETS.items()])
    assets = dict(pairs)
    available = sum(1 for x in assets.values() if x.get("status") == "AVAILABLE")
    return {
        "status": "AVAILABLE" if available else "UNAVAILABLE",
        "available": available,
        "total": len(assets),
        "assets": assets,
        "method_note": "Public cross-asset snapshot for research context only. Values are never execution-grade and do not authorize or veto production trades.",
    }
