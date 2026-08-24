from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import httpx

INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
MATCH_TERMS = ("GIFT", "NSEIX", "NSE IX", "GIFTNIFTY", "GIFT NIFTY")


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(k, "")) for k in ("exchange", "trading_symbol", "groww_symbol", "name", "underlying_symbol")).upper()


def _candidate_rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    matches = []
    for row in reader:
        blob = _row_text(row)
        if any(term in blob for term in MATCH_TERMS):
            matches.append({
                "exchange": row.get("exchange"),
                "trading_symbol": row.get("trading_symbol"),
                "groww_symbol": row.get("groww_symbol"),
                "name": row.get("name"),
                "instrument_type": row.get("instrument_type"),
                "segment": row.get("segment"),
                "underlying_symbol": row.get("underlying_symbol"),
                "expiry_date": row.get("expiry_date"),
                "buy_allowed": row.get("buy_allowed"),
                "sell_allowed": row.get("sell_allowed"),
            })
    return matches[:25]


async def groww_gift_discovery(provider) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(INSTRUMENT_CSV_URL, headers={"User-Agent": "Mozilla/5.0 AlphaPilot/1.0"})
            response.raise_for_status()
        matches = _candidate_rows(response.text)
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "supported": False,
            "matches": [],
            "source": "Groww official instrument master",
            "source_url": INSTRUMENT_CSV_URL,
            "error": str(exc) or exc.__class__.__name__,
            "checked_at": now,
            "research_only": True,
            "production_rules_changed": False,
        }

    result = {
        "status": "FOUND" if matches else "NOT_FOUND",
        "supported": bool(matches),
        "matches": matches,
        "source": "Groww official instrument master",
        "source_url": INSTRUMENT_CSV_URL,
        "checked_at": now,
        "research_only": True,
        "production_rules_changed": False,
        "note": "AlphaPilot only treats GIFT NIFTY as Groww-supported when an explicit GIFT/NSEIX instrument appears in Groww's own instrument master.",
    }

    if not matches:
        return result

    # Best-effort live quote verification using the same authenticated Groww provider.
    verified = []
    if hasattr(provider, "_headers") and hasattr(provider, "BASE_URL"):
        for row in matches[:5]:
            exchange = str(row.get("exchange") or "").strip()
            segment = str(row.get("segment") or "").strip()
            symbol = str(row.get("trading_symbol") or "").strip()
            if not exchange or not segment or not symbol:
                continue
            try:
                async with httpx.AsyncClient(timeout=12) as client:
                    r = await client.get(
                        f"{provider.BASE_URL}/v1/live-data/quote",
                        headers=await provider._headers(),
                        params={"exchange": exchange, "segment": segment, "trading_symbol": symbol},
                    )
                verified.append({
                    "exchange": exchange,
                    "segment": segment,
                    "trading_symbol": symbol,
                    "http_status": r.status_code,
                    "quote_ok": r.status_code == 200,
                })
            except Exception as exc:
                verified.append({"exchange": exchange, "segment": segment, "trading_symbol": symbol, "quote_ok": False, "error": str(exc) or exc.__class__.__name__})
    result["quote_verification"] = verified
    return result
