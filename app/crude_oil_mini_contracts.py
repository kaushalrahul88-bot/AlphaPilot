from __future__ import annotations

import csv
from datetime import date, datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import httpx

from .fno_history_probe import INSTRUMENT_CSV_URL

IST = ZoneInfo("Asia/Kolkata")
CRUDE_OIL_MINI = "CRUDEOILM"
MCX = "MCX"
COMMODITY = "COMMODITY"
OPTION_TYPES = {"CE", "PE"}
FUTURE_TYPES = {"FUT", "FUTURE", "FUTURES"}


def _as_date(value) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _as_int(value) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _underlying(row: dict) -> str:
    return str(
        row.get("underlying_symbol")
        or row.get("underlying")
        or row.get("name")
        or ""
    ).upper().replace(" ", "").strip()


def normalize_crude_oil_mini_row(row: dict) -> dict | None:
    """Normalize only the dedicated MCX Crude Oil Mini family.

    Regular CRUDEOIL is intentionally rejected.  AlphaPilot must never infer that
    a regular-Crude contract is the Mini product merely because their prices are
    related.
    """
    if str(row.get("exchange") or "").upper().strip() != MCX:
        return None
    if str(row.get("segment") or "").upper().strip() != COMMODITY:
        return None
    underlying = _underlying(row)
    if underlying != CRUDE_OIL_MINI:
        return None

    instrument_type = str(row.get("instrument_type") or "").upper().strip()
    trading_symbol = str(
        row.get("trading_symbol") or row.get("internal_trading_symbol") or ""
    ).upper().strip()
    expiry = _as_date(row.get("expiry_date") or row.get("expiry"))
    if not trading_symbol or expiry is None:
        return None

    is_future = instrument_type in FUTURE_TYPES or trading_symbol.endswith("FUT")
    is_option = instrument_type in OPTION_TYPES
    if not (is_future or is_option):
        return None

    normalized = {
        "underlying": CRUDE_OIL_MINI,
        "exchange": MCX,
        "segment": COMMODITY,
        "instrument_type": "FUT" if is_future else instrument_type,
        "trading_symbol": trading_symbol,
        "groww_symbol": str(row.get("groww_symbol") or "").strip(),
        "expiry": expiry.isoformat(),
        "lot_size": _as_int(row.get("lot_size")),
        "tick_size_raw": _as_float(row.get("tick_size")),
        "buy_allowed": str(row.get("buy_allowed") or "").strip() in {"", "1", "true", "True"},
    }
    if is_option:
        strike = _as_float(row.get("strike_price") or row.get("strike"))
        if strike is None or strike <= 0 or not normalized["groww_symbol"]:
            return None
        normalized.update(option_type=instrument_type, strike=strike)
    else:
        normalized.update(option_type=None, strike=None)
    return normalized


def normalize_crude_oil_mini_rows(rows: Iterable[dict]) -> list[dict]:
    normalized = []
    for row in rows or []:
        item = normalize_crude_oil_mini_row(row)
        if item:
            normalized.append(item)
    return normalized


async def fetch_crude_oil_mini_master() -> list[dict]:
    """Read the current Groww instrument master and return only CRUDEOILM rows."""
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=45) as client:
        async with client.stream("GET", INSTRUMENT_CSV_URL) as response:
            response.raise_for_status()
            fieldnames = None
            async for line in response.aiter_lines():
                if not line:
                    continue
                values = next(csv.reader([line]))
                if fieldnames is None:
                    fieldnames = [str(v).lstrip("\ufeff").strip() for v in values]
                    continue
                if len(values) < len(fieldnames):
                    values += [""] * (len(fieldnames) - len(values))
                elif len(values) > len(fieldnames):
                    values = values[: len(fieldnames)]
                item = normalize_crude_oil_mini_row(dict(zip(fieldnames, values)))
                if item:
                    rows.append(item)
    return rows


def resolve_crude_oil_mini_universe(rows: Iterable[dict], as_of: date | datetime | str) -> dict:
    """Resolve the current Mini future and independently resolve Mini option expiries.

    Futures expiry and option expiry are deliberately separate fields.  This is the
    guard that prevents a futures expiry (for example 21 Sep) from being mistaken for
    the option-chain expiry (for example 17 Sep).
    """
    if isinstance(as_of, datetime):
        day = as_of.astimezone(IST).date() if as_of.tzinfo else as_of.date()
    elif isinstance(as_of, date):
        day = as_of
    else:
        day = date.fromisoformat(str(as_of)[:10])

    clean = normalize_crude_oil_mini_rows(rows)
    active = [r for r in clean if _as_date(r["expiry"]) and _as_date(r["expiry"]) >= day]
    futures = sorted(
        (r for r in active if r["instrument_type"] == "FUT"),
        key=lambda r: (_as_date(r["expiry"]), r["trading_symbol"]),
    )
    options = [r for r in active if r["instrument_type"] in OPTION_TYPES]
    if not futures:
        raise RuntimeError(f"No active {CRUDE_OIL_MINI} MCX future found as of {day}")
    if not options:
        raise RuntimeError(f"No active {CRUDE_OIL_MINI} MCX options found as of {day}")

    option_expiries = sorted({_as_date(r["expiry"]) for r in options})
    nearest_option_expiry = option_expiries[0]
    nearest_options = [r for r in options if _as_date(r["expiry"]) == nearest_option_expiry]
    types = sorted({r["instrument_type"] for r in nearest_options})
    missing = sorted(OPTION_TYPES - set(types))
    if missing:
        raise RuntimeError(
            f"Nearest {CRUDE_OIL_MINI} option expiry {nearest_option_expiry} is missing {missing}"
        )

    future = futures[0]
    return {
        "status": "READY",
        "product": "CRUDE_OIL_MINI",
        "underlying_symbol": CRUDE_OIL_MINI,
        "as_of": day.isoformat(),
        "future": future,
        "future_expiry": future["expiry"],
        "option_expiries": [x.isoformat() for x in option_expiries],
        "nearest_option_expiry": nearest_option_expiry.isoformat(),
        "nearest_option_types": types,
        "nearest_option_contracts": len(nearest_options),
        "nearest_option_lot_sizes": sorted({r["lot_size"] for r in nearest_options if r.get("lot_size")}),
        "future_and_option_expiry_are_independent": True,
        "regular_crude_alias_allowed": False,
        "research_only": True,
        "live_execution_enabled": False,
    }


async def audit_current_crude_oil_mini_universe(now: datetime | None = None) -> dict:
    observed = now or datetime.now(IST)
    rows = await fetch_crude_oil_mini_master()
    result = resolve_crude_oil_mini_universe(rows, observed)
    result["instrument_master_rows"] = len(rows)
    result["observed_at"] = observed.astimezone(IST).isoformat()
    return result
