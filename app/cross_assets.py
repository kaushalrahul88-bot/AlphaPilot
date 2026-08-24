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
    "BRENT": {"ticker": "BZ=F", "label": "Brent crude", "direction": "india_energy_inverse"},
    "NASDAQ_FUTURES": {"ticker": "NQ=F", "label": "Nasdaq futures", "direction": "risk_positive"},
}


def _bias(change_pct: float | None, direction: str) -> str:
    if change_pct is None or abs(change_pct) < 0.15:
        return "NEUTRAL"
    up = change_pct > 0
    if direction == "risk_positive":
        return "RISK_ON" if up else "RISK_OFF"
    if direction in {"risk_inverse", "india_energy_inverse"}:
        return "RISK_OFF" if up else "RISK_ON"
    return "NEUTRAL"


def _agreement(assets: dict[str, dict]) -> dict:
    usable = [x for x in assets.values() if x.get("status") == "AVAILABLE" and x.get("bias") in {"RISK_ON", "RISK_OFF", "NEUTRAL"}]
    risk_on = sum(1 for x in usable if x.get("bias") == "RISK_ON")
    risk_off = sum(1 for x in usable if x.get("bias") == "RISK_OFF")
    neutral = sum(1 for x in usable if x.get("bias") == "NEUTRAL")
    lead = risk_on - risk_off
    if len(usable) < 4:
        state = "INSUFFICIENT_COVERAGE"
    elif risk_on >= 4 and lead >= 2:
        state = "RISK_ON_AGREEMENT"
    elif risk_off >= 4 and lead <= -2:
        state = "RISK_OFF_AGREEMENT"
    else:
        state = "MIXED_CROSS_ASSET"
    return {
        "state": state,
        "risk_on": risk_on,
        "risk_off": risk_off,
        "neutral": neutral,
        "usable": len(usable),
        "score": lead,
        "research_only": True,
        "method_note": "Agreement is a fixed descriptive research state, not a trading gate. No threshold was tuned from Candidate A/B OOS results.",
    }


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
        "agreement": _agreement(assets),
        "method_note": "Public cross-asset snapshot for research context only. Values and agreement states are never execution-grade and do not authorize or veto production trades.",
    }
