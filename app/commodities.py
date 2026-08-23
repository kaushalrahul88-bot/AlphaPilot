import csv
import io
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
SUPPORTED_COMMODITIES = {"CRUDEOIL", "NATURALGAS"}
_CACHE_TTL_SECONDS = 6 * 60 * 60
_instrument_cache = {"loaded_at": 0.0, "rows": []}


def _as_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _parse_expiry(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


async def _load_instruments(force=False):
    now = time.time()
    if not force and _instrument_cache["rows"] and now - _instrument_cache["loaded_at"] < _CACHE_TTL_SECONDS:
        return _instrument_cache["rows"]

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(INSTRUMENT_CSV_URL)
    response.raise_for_status()

    rows = list(csv.DictReader(io.StringIO(response.text)))
    _instrument_cache["rows"] = rows
    _instrument_cache["loaded_at"] = now
    return rows


async def resolve_nearest_mcx_future(symbol):
    symbol = str(symbol or "").strip().upper()
    if symbol not in SUPPORTED_COMMODITIES:
        raise ValueError(f"Unsupported commodity {symbol}. Supported: {', '.join(sorted(SUPPORTED_COMMODITIES))}")

    rows = await _load_instruments()
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    candidates = []

    for row in rows:
        if str(row.get("exchange") or "").upper() != "MCX":
            continue
        if str(row.get("segment") or "").upper() != "COMMODITY":
            continue
        underlying = str(row.get("underlying_symbol") or row.get("name") or "").upper().replace(" ", "")
        trading_symbol = str(row.get("trading_symbol") or "").upper()
        instrument_type = str(row.get("instrument_type") or "").upper()
        if symbol not in {underlying, trading_symbol.split("25")[0], trading_symbol.split("26")[0], trading_symbol.split("27")[0]} and not trading_symbol.startswith(symbol):
            continue
        if instrument_type not in {"FUT", "FUTURE", "FUTURES"} and not trading_symbol.endswith("FUT"):
            continue
        expiry = _parse_expiry(row.get("expiry_date"))
        if not expiry or expiry < today:
            continue
        if row.get("buy_allowed") not in (None, "") and not _as_bool(row.get("buy_allowed")):
            continue
        candidates.append((expiry, row))

    if not candidates:
        raise RuntimeError(f"No active MCX future found for {symbol}")

    candidates.sort(key=lambda item: item[0])
    expiry, row = candidates[0]
    return {
        "underlying": symbol,
        "exchange": "MCX",
        "segment": "COMMODITY",
        "trading_symbol": str(row.get("trading_symbol") or ""),
        "groww_symbol": str(row.get("groww_symbol") or ""),
        "expiry_date": expiry.isoformat(),
        "lot_size": int(float(row.get("lot_size") or 0)) if str(row.get("lot_size") or "").strip() else None,
        "tick_size": float(row.get("tick_size") or 0) if str(row.get("tick_size") or "").strip() else None,
        "instrument_type": str(row.get("instrument_type") or "FUT"),
    }


async def commodity_quote(provider, symbol):
    contract = await resolve_nearest_mcx_future(symbol)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{provider.BASE_URL}/v1/live-data/quote",
            headers=await provider._headers(),
            params={
                "exchange": contract["exchange"],
                "segment": contract["segment"],
                "trading_symbol": contract["trading_symbol"],
            },
        )
    response.raise_for_status()
    return {"provider": "GROWW", "contract": contract, "data": response.json()}


async def commodity_candles(provider, symbol, timeframe="5m"):
    contract = await resolve_nearest_mcx_future(symbol)
    interval_map = {
        "5m": ("5minute", 7),
        "15m": ("15minute", 14),
        "1h": ("1hour", 60),
    }
    candle_interval, days = interval_map.get(timeframe, ("5minute", 7))
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    start = now - timedelta(days=days)
    params = {
        "exchange": contract["exchange"],
        "segment": contract["segment"],
        "groww_symbol": contract["groww_symbol"],
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "candle_interval": candle_interval,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{provider.BASE_URL}/v1/historical/candles",
            headers=await provider._headers(),
            params=params,
        )
    response.raise_for_status()
    data = response.json()
    payload = data.get("payload", data)
    candles = payload.get("candles", []) if isinstance(payload, dict) else []
    return {"provider": "GROWW", "contract": contract, "timeframe": timeframe, "candles": candles}


async def commodity_probe(provider, symbol):
    contract = await resolve_nearest_mcx_future(symbol)
    quote_result = None
    candle_result = None
    errors = []

    try:
        quote_result = await commodity_quote(provider, symbol)
    except Exception as exc:
        errors.append({"check": "quote", "error": str(exc)})

    try:
        candle_result = await commodity_candles(provider, symbol, "5m")
    except Exception as exc:
        errors.append({"check": "candles", "error": str(exc)})

    candle_count = len(candle_result.get("candles", [])) if candle_result else 0
    return {
        "symbol": symbol.upper(),
        "contract": contract,
        "quote_ok": quote_result is not None,
        "candles_ok": candle_result is not None and candle_count > 0,
        "candle_count": candle_count,
        "quote": quote_result,
        "errors": errors,
        "ready_for_phase1": quote_result is not None and candle_result is not None and candle_count > 0,
    }
