from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone

import httpx

INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"


def _norm(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def _is_explicit_gift_row(row: dict) -> bool:
    exchange = _norm(row.get("exchange"))
    trading = _norm(row.get("trading_symbol"))
    groww = _norm(row.get("groww_symbol"))
    name = _norm(row.get("name"))
    underlying = _norm(row.get("underlying_symbol"))

    # Exchange-level evidence is strongest. Keep this strict so symbols such as
    # IXIGO (NSE cash) can never be mistaken for NSE IX simply because fields
    # concatenate to text like "NSE IXIGO".
    if exchange in {"NSEIX", "NSE IX"}:
        return "NIFTY" in trading or "NIFTY" in groww or "NIFTY" in name or "NIFTY" in underlying

    explicit_fields = (trading, groww, name, underlying)
    explicit_phrases = ("GIFT NIFTY", "GIFTNIFTY", "NIFTY GIFT", "NSEIX NIFTY", "NSE IX NIFTY")
    return any(any(phrase in field for phrase in explicit_phrases) for field in explicit_fields)


def _candidate_rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    matches = []
    for row in reader:
        if not _is_explicit_gift_row(row):
            continue
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


async def groww_gift_discovery(provider=None) -> dict:
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
        "note": "Strict field-level matching only: explicit GIFT NIFTY or NSE IX + NIFTY evidence is required. Ordinary NSE symbols such as IXIGO cannot qualify.",
    }

    if not matches or provider is None:
        return result

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
