"""Read-only market universe for the AlphaPilot dashboard.

F&O underlyings are derived from Groww's documented public instrument-master CSV.
No authentication, account data, orders, or trading permissions are used.
"""
from __future__ import annotations

import asyncio
import csv
import io
from datetime import date, datetime, timezone
from threading import Lock
from time import monotonic

import httpx

GROWW_INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
MODE = "ALPHAPILOT_DASHBOARD_MARKET_UNIVERSE_V1"
_CACHE_TTL_SECONDS = 6 * 60 * 60
_CACHE_LOCK = Lock()
_CACHE: dict[str, object] = {"loaded_at": 0.0, "payload": None}

_INDEX_NAMES = {
    "NIFTY": "Nifty 50",
    "BANKNIFTY": "Nifty Bank",
    "FINNIFTY": "Nifty Financial Services",
    "MIDCPNIFTY": "Nifty Midcap Select",
    "NIFTYNXT50": "Nifty Next 50",
}

COMMODITIES = (
    {"symbol": "COPPER", "name": "Copper", "state": "CONNECTED"},
    {"symbol": "CRUDEOILM", "name": "Crude Oil Mini", "state": "CONNECTED"},
    {"symbol": "CRUDEOIL", "name": "Crude Oil", "state": "PLANNED"},
    {"symbol": "NATGAS", "name": "Natural Gas", "state": "PLANNED"},
    {"symbol": "SILVER", "name": "Silver", "state": "PLANNED"},
    {"symbol": "GOLD", "name": "Gold", "state": "PLANNED"},
)

CRYPTO = (
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


def parse_groww_fno_underlyings(csv_text: str, *, as_of: date) -> list[dict]:
    """Return unique active NSE F&O underlyings from the documented Groww master."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows or "segment" not in (reader.fieldnames or ()):
        raise ValueError("Groww instrument CSV has an unexpected schema")

    cash_names: dict[str, str] = {}
    for row in rows:
        if str(row.get("exchange") or "").strip().upper() != "NSE":
            continue
        if str(row.get("segment") or "").strip().upper() != "CASH":
            continue
        symbol = str(row.get("trading_symbol") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        if symbol and name and name.lower() != "nan":
            cash_names.setdefault(symbol, name)

    active: set[str] = set()
    for row in rows:
        if str(row.get("exchange") or "").strip().upper() != "NSE":
            continue
        if str(row.get("segment") or "").strip().upper() != "FNO":
            continue
        expiry = _parse_expiry(row.get("expiry_date"))
        if expiry is None or expiry < as_of:
            continue
        if not _truthy_csv(row.get("buy_allowed")):
            continue
        underlying = str(row.get("underlying_symbol") or "").strip().upper()
        if underlying and underlying.lower() != "nan":
            active.add(underlying)

    result = []
    for symbol in sorted(active, key=lambda item: (item not in _INDEX_NAMES, item)):
        result.append({
            "symbol": symbol,
            "name": _INDEX_NAMES.get(symbol) or cash_names.get(symbol) or symbol,
            "state": "AVAILABLE",
            "exchange": "NSE",
            "segment": "FNO",
        })
    if not result:
        raise ValueError("Groww instrument CSV contained no active NSE F&O underlyings")
    return result


def _download_universe_sync(as_of: date) -> dict:
    response = httpx.get(
        GROWW_INSTRUMENT_CSV_URL,
        timeout=20.0,
        headers={"Accept": "text/csv"},
        follow_redirects=True,
    )
    response.raise_for_status()
    fno = parse_groww_fno_underlyings(response.text, as_of=as_of)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "mode": MODE,
        "status": "ACTIVE",
        "as_of": as_of.isoformat(),
        "generated_at": now,
        "source": {
            "fno": "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER",
            "authentication_required": False,
            "account_data_accessed": False,
        },
        "categories": {
            "FNO": fno,
            "COMMODITIES": [dict(row) for row in COMMODITIES],
            "CRYPTO": [dict(row) for row in CRYPTO],
        },
        "counts": {
            "FNO": len(fno),
            "COMMODITIES": len(COMMODITIES),
            "CRYPTO": len(CRYPTO),
        },
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
            return {
                **stale,
                "status": "DEGRADED_STALE_CACHE",
                "refresh_error": exc.__class__.__name__,
            }
        return {
            "mode": MODE,
            "status": "UNAVAILABLE",
            "as_of": effective_date.isoformat(),
            "source": {"fno": "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER"},
            "categories": {
                "FNO": [],
                "COMMODITIES": [dict(row) for row in COMMODITIES],
                "CRYPTO": [dict(row) for row in CRYPTO],
            },
            "counts": {"FNO": 0, "COMMODITIES": len(COMMODITIES), "CRYPTO": len(CRYPTO)},
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
        "version": "DASHBOARD_MARKET_UNIVERSE_CONTRACT_V1",
        "fno_source": "GROWW_DOCUMENTED_PUBLIC_INSTRUMENT_MASTER",
        "authentication_required": False,
        "account_data_accessed": False,
        "read_only": True,
        "order_placement_enabled": False,
        "live_execution": False,
        "cache_ttl_seconds": _CACHE_TTL_SECONDS,
    }
