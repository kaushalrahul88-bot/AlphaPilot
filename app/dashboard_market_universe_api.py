"""Read-only market universe for the AlphaPilot dashboard.

The dashboard universe is discovery/navigation metadata only:
- NSE F&O and MCX Commodity underlyings come from Groww's documented public
  instrument-master CSV.
- Crypto Options underlyings come from Delta Exchange India's documented public
  live Products endpoint.
No authentication, account data, orders, or trading permissions are used.
"""
from __future__ import annotations

import asyncio
import csv
import io
import re
from datetime import date, datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any

import httpx

from app.providers.groww import GrowwProvider

GROWW_INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
DELTA_INDIA_PRODUCTS_URL = "https://api.india.delta.exchange/v2/products"
MODE = "ALPHAPILOT_DASHBOARD_MARKET_UNIVERSE_V3"
_CACHE_TTL_SECONDS = 6 * 60 * 60
_CACHE_LOCK = Lock()
_CACHE: dict[str, object] = {"loaded_at": 0.0, "payload": None}
_OPTION_SYMBOL = re.compile(r"^[CP]-(?P<underlying>[A-Z0-9]+)-")

_INDEX_NAMES = {
    "NIFTY": "Nifty 50",
    "BANKNIFTY": "Nifty Bank",
    "FINNIFTY": "Nifty Financial Services",
    "MIDCPNIFTY": "Nifty Midcap Select",
    "NIFTYNXT50": "Nifty Next 50",
}
_COMMODITY_NAMES = {
    "ALUMINIUM": "Aluminium",
    "COPPER": "Copper",
    "CRUDEOIL": "Crude Oil",
    "CRUDEOILM": "Crude Oil Mini",
    "GOLD": "Gold",
    "GOLDM": "Gold Mini",
    "GOLDGUINEA": "Gold Guinea",
    "GOLDPETAL": "Gold Petal",
    "LEAD": "Lead",
    "NATGAS": "Natural Gas",
    "NATURALGAS": "Natural Gas",
    "NICKEL": "Nickel",
    "SILVER": "Silver",
    "SILVERM": "Silver Mini",
    "SILVERMIC": "Silver Micro",
    "ZINC": "Zinc",
}
_CRYPTO_NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "XAUT": "Tether Gold", "SOL": "Solana"}
_CONNECTED_FNO = set(GrowwProvider.NSE_CASH_SYMBOLS) | {"NIFTY", "BANKNIFTY"}
_CONNECTED_COMMODITIES = {"COPPER", "CRUDEOILM"}
_CONNECTED_CRYPTO = {"BTC"}

COMMODITY_FALLBACK = (
    {"symbol": "COPPER", "name": "Copper", "state": "CONNECTED"},
    {"symbol": "CRUDEOILM", "name": "Crude Oil Mini", "state": "CONNECTED"},
    {"symbol": "CRUDEOIL", "name": "Crude Oil", "state": "PLANNED"},
    {"symbol": "NATGAS", "name": "Natural Gas", "state": "PLANNED"},
    {"symbol": "SILVER", "name": "Silver", "state": "PLANNED"},
    {"symbol": "GOLD", "name": "Gold", "state": "PLANNED"},
)
CRYPTO_FALLBACK = (
    {"symbol": "BTC", "name": "Bitcoin", "state": "CONNECTED"},
    {"symbol": "ETH", "name": "Ethereum", "state": "PLANNED"},
    {"symbol": "XAUT", "name": "Tether Gold", "state": "PLANNED"},
)


def _truthy_csv(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _parse_expiry(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _csv_rows(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows or "segment" not in (reader.fieldnames or ()):
        raise ValueError("Groww instrument CSV has an unexpected schema")
    return rows


def parse_groww_fno_underlyings(csv_text: str, *, as_of: date) -> list[dict]:
    rows = _csv_rows(csv_text)
    cash_names: dict[str, str] = {}
    for row in rows:
        if str(row.get("exchange") or "").strip().upper() != "NSE" or str(row.get("segment") or "").strip().upper() != "CASH":
            continue
        symbol = str(row.get("trading_symbol") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        if symbol and name and name.lower() != "nan":
            cash_names.setdefault(symbol, name)

    active: set[str] = set()
    for row in rows:
        if str(row.get("exchange") or "").strip().upper() != "NSE" or str(row.get("segment") or "").strip().upper() != "FNO":
            continue
        expiry = _parse_expiry(row.get("expiry_date"))
        if expiry is None or expiry < as_of or not _truthy_csv(row.get("buy_allowed")):
            continue
        underlying = str(row.get("underlying_symbol") or "").strip().upper()
        if underlying and underlying.lower() != "nan":
            active.add(underlying)

    result = [{
        "symbol": symbol,
        "name": _INDEX_NAMES.get(symbol) or cash_names.get(symbol) or symbol,
        "state": "CONNECTED" if symbol in _CONNECTED_FNO else "AVAILABLE",
        "live_scan_connected": symbol in _CONNECTED_FNO,
        "exchange": "NSE",
        "segment": "FNO",
    } for symbol in sorted(active, key=lambda item: (item not in _INDEX_NAMES, item))]
    if not result:
        raise ValueError("Groww instrument CSV contained no active NSE F&O underlyings")
    return result


def parse_groww_commodity_underlyings(csv_text: str, *, as_of: date) -> list[dict]:
    rows = _csv_rows(csv_text)
    active: set[str] = set()
    for row in rows:
        if str(row.get("exchange") or "").strip().upper() != "MCX" or str(row.get("segment") or "").strip().upper() != "COMMODITY":
            continue
        expiry = _parse_expiry(row.get("expiry_date"))
        if expiry is None or expiry < as_of or not _truthy_csv(row.get("buy_allowed")):
            continue
        underlying = str(row.get("underlying_symbol") or "").strip().upper()
        if not underlying or underlying.lower() == "nan":
            underlying = str(row.get("name") or "").strip().upper().replace(" ", "")
        if underlying and underlying.lower() != "nan":
            active.add(underlying)
    result = [{
        "symbol": symbol,
        "name": _COMMODITY_NAMES.get(symbol) or symbol.replace("_", " ").title(),
        "state": "CONNECTED" if symbol in _CONNECTED_COMMODITIES else "PLANNED",
        "exchange": "MCX",
        "segment": "COMMODITY",
    } for symbol in sorted(active)]
    if not result:
        raise ValueError("Groww instrument CSV contained no active MCX Commodity underlyings")
    return result


def parse_delta_option_underlyings(products: list[dict[str, Any]]) -> list[dict]:
    active: set[str] = set()
    for row in products:
        if not isinstance(row, dict):
            continue
        if str(row.get("contract_type") or "") not in {"call_options", "put_options"}:
            continue
        if str(row.get("state") or "live").lower() != "live":
            continue
        underlying = row.get("underlying_asset")
        symbol = str(underlying.get("symbol") or "").strip().upper() if isinstance(underlying, dict) else ""
        if not symbol:
            match = _OPTION_SYMBOL.match(str(row.get("symbol") or "").strip().upper())
            symbol = match.group("underlying") if match else ""
        if symbol:
            active.add(symbol)
    result = [{
        "symbol": symbol,
        "name": _CRYPTO_NAMES.get(symbol) or symbol,
        "state": "CONNECTED" if symbol in _CONNECTED_CRYPTO else "PLANNED",
        "venue": "DELTA_EXCHANGE_INDIA",
        "segment": "OPTIONS",
    } for symbol in sorted(active, key=lambda item: (item != "BTC", item))]
    if not result:
        raise ValueError("Delta India returned no live Options underlyings")
    return result


def _download_delta_products(client: httpx.Client) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(30):
        params = {"contract_types": "call_options,put_options", "states": "live", "page_size": "100"}
        if after:
            params["after"] = after
        response = client.get(DELTA_INDIA_PRODUCTS_URL, params=params, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
        page = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(page, list):
            raise ValueError("Delta India products response has an unexpected schema")
        rows.extend(row for row in page if isinstance(row, dict))
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        next_after = str(meta.get("after") or "").strip()
        if not page or not next_after or next_after == after:
            break
        after = next_after
    return rows


def _download_universe_sync(as_of: date) -> dict:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        groww_response = client.get(GROWW_INSTRUMENT_CSV_URL, headers={"Accept": "text/csv"})
        groww_response.raise_for_status()
        csv_text = groww_response.text
        fno = parse_groww_fno_underlyings(csv_text, as_of=as_of)
        try:
            commodities = parse_groww_commodity_underlyings(csv_text, as_of=as_of)
            commodity_status = "LIVE_MASTER"
        except Exception:
            commodities = [dict(row) for row in COMMODITY_FALLBACK]
            commodity_status = "FALLBACK"
        try:
            crypto = parse_delta_option_underlyings(_download_delta_products(client))
            crypto_status = "LIVE_MASTER"
        except Exception:
            crypto = [dict(row) for row in CRYPTO_FALLBACK]
            crypto_status = "FALLBACK"

    now = datetime.now(timezone.utc).isoformat()
    return {
        "mode": MODE,
        "status": "ACTIVE",
        "as_of": as_of.isoformat(),
        "generated_at": now,
        "source": {
            "fno": "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER",
            "commodities": "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER" if commodity_status == "LIVE_MASTER" else "STATIC_SAFE_FALLBACK",
            "crypto": "DELTA_INDIA_DOCUMENTED_PUBLIC_PRODUCTS" if crypto_status == "LIVE_MASTER" else "STATIC_SAFE_FALLBACK",
            "authentication_required": False,
            "account_data_accessed": False,
        },
        "category_status": {"FNO": "LIVE_MASTER", "COMMODITIES": commodity_status, "CRYPTO": crypto_status},
        "categories": {"FNO": fno, "COMMODITIES": commodities, "CRYPTO": crypto},
        "counts": {"FNO": len(fno), "COMMODITIES": len(commodities), "CRYPTO": len(crypto)},
        "read_only": True,
        "live_execution": False,
    }


def _cached_universe_sync(as_of: date) -> dict:
    now = monotonic()
    with _CACHE_LOCK:
        payload = _CACHE.get("payload")
        loaded_at = float(_CACHE.get("loaded_at") or 0.0)
        if isinstance(payload, dict) and now - loaded_at < _CACHE_TTL_SECONDS and payload.get("as_of") == as_of.isoformat():
            return dict(payload)
    fresh = _download_universe_sync(as_of)
    with _CACHE_LOCK:
        _CACHE["loaded_at"] = now
        _CACHE["payload"] = fresh
    return dict(fresh)


async def read_dashboard_market_universe(*, as_of: date | None = None) -> dict:
    effective_date = as_of or datetime.now(timezone.utc).date()
    try:
        return await asyncio.to_thread(_cached_universe_sync, effective_date)
    except Exception as exc:
        with _CACHE_LOCK:
            stale = _CACHE.get("payload")
        if isinstance(stale, dict):
            return {**stale, "status": "DEGRADED_STALE_CACHE", "refresh_error": exc.__class__.__name__}
        return {
            "mode": MODE,
            "status": "UNAVAILABLE",
            "as_of": effective_date.isoformat(),
            "source": {
                "fno": "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER",
                "commodities": "STATIC_SAFE_FALLBACK",
                "crypto": "STATIC_SAFE_FALLBACK",
            },
            "category_status": {"FNO": "UNAVAILABLE", "COMMODITIES": "FALLBACK", "CRYPTO": "FALLBACK"},
            "categories": {
                "FNO": [],
                "COMMODITIES": [dict(row) for row in COMMODITY_FALLBACK],
                "CRYPTO": [dict(row) for row in CRYPTO_FALLBACK],
            },
            "counts": {"FNO": 0, "COMMODITIES": len(COMMODITY_FALLBACK), "CRYPTO": len(CRYPTO_FALLBACK)},
            "refresh_error": exc.__class__.__name__,
            "read_only": True,
            "live_execution": False,
        }


def register_dashboard_market_universe_routes(app) -> None:
    @app.get("/v1/dashboard/market-universe")
    async def dashboard_market_universe():
        return await read_dashboard_market_universe()


def architecture_contract() -> dict:
    return {
        "version": "DASHBOARD_MARKET_UNIVERSE_CONTRACT_V3",
        "fno_source": "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER",
        "fno_connected_state_basis": "EXISTING_GROWW_LIVE_SCAN_MAPPING",
        "commodity_source": "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER",
        "crypto_source": "DELTA_INDIA_DOCUMENTED_PUBLIC_PRODUCTS",
        "authentication_required": False,
        "account_data_accessed": False,
        "read_only": True,
        "order_placement_enabled": False,
        "live_execution": False,
        "cache_ttl_seconds": _CACHE_TTL_SECONDS,
    }
