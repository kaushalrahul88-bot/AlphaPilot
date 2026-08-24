from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")
INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"


def _norm_expiry(value: str) -> str:
    return str(value or "").strip()[:10]


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _resolve_option_contract(symbol: str, expiry: str, strike: float, option_type: str):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(INSTRUMENT_CSV_URL)
        response.raise_for_status()

    target_symbol = symbol.upper().strip()
    target_expiry = _norm_expiry(expiry)
    target_type = option_type.upper().strip()
    target_strike = round(float(strike), 4)

    reader = csv.DictReader(io.StringIO(response.text))
    candidates = []
    for row in reader:
        if str(row.get("exchange", "")).upper() != "NSE":
            continue
        if str(row.get("segment", "")).upper() != "FNO":
            continue
        if str(row.get("instrument_type", "")).upper() != target_type:
            continue
        if str(row.get("underlying_symbol", "")).upper().strip() != target_symbol:
            continue
        if _norm_expiry(row.get("expiry_date", "")) != target_expiry:
            continue
        row_strike = _as_float(row.get("strike_price"))
        if row_strike is None or round(row_strike, 4) != target_strike:
            continue
        candidates.append(row)

    if not candidates:
        return None

    row = candidates[0]
    groww_symbol = (
        row.get("groww_symbol")
        or row.get("groww_ticker")
        or row.get("symbol")
        or row.get("trading_symbol")
    )
    trading_symbol = row.get("trading_symbol") or row.get("tradingsymbol") or row.get("symbol")
    return {
        "underlying": target_symbol,
        "expiry": target_expiry,
        "strike": target_strike,
        "option_type": target_type,
        "groww_symbol": groww_symbol,
        "trading_symbol": trading_symbol,
        "lot_size": _as_float(row.get("lot_size")),
        "instrument_type": row.get("instrument_type"),
        "segment": row.get("segment"),
        "exchange": row.get("exchange"),
    }


async def probe_historical_option_candles(
    provider,
    symbol: str,
    expiry: str,
    strike: float,
    option_type: str,
    interval: str = "5minute",
    lookback_days: int = 5,
):
    option_type = option_type.upper().strip()
    if option_type not in {"CE", "PE"}:
        raise ValueError("option_type must be CE or PE")
    if interval not in {"1minute", "5minute", "10minute", "15minute", "30minute", "1hour", "1day"}:
        raise ValueError("Unsupported candle interval")

    contract = await _resolve_option_contract(symbol, expiry, strike, option_type)
    if not contract:
        return {
            "supported": False,
            "stage": "CONTRACT_RESOLUTION",
            "reason": "Exact NSE F&O option contract was not found in Groww's current instrument master.",
            "request": {"symbol": symbol.upper(), "expiry": _norm_expiry(expiry), "strike": strike, "option_type": option_type},
            "caveat": "Failure here does not prove Groww lacks expired-option history; expired contracts may simply be absent from the current instrument master.",
        }

    groww_symbol = contract.get("groww_symbol")
    if not groww_symbol:
        return {
            "supported": False,
            "stage": "CONTRACT_IDENTIFIER",
            "reason": "Contract resolved, but no Groww historical symbol identifier was present in the instrument master row.",
            "contract": contract,
        }

    expiry_dt = datetime.fromisoformat(_norm_expiry(expiry)).replace(tzinfo=IST)
    end = expiry_dt + timedelta(hours=15, minutes=30)
    start = end - timedelta(days=max(1, min(int(lookback_days), 20)))

    # Respect the shared Groww request budget when this provider exposes it.
    throttle = getattr(provider, "_throttle", None)
    if callable(throttle):
        await throttle()

    params = {
        "exchange": "NSE",
        "segment": "FNO",
        "groww_symbol": groww_symbol,
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
        "candle_interval": interval,
    }

    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.get(
            f"{provider.BASE_URL}/v1/historical/candles",
            headers=await provider._headers(),
            params=params,
        )

    body_preview = response.text[:500]
    if response.status_code != 200:
        return {
            "supported": False,
            "stage": "HISTORICAL_CANDLES",
            "http_status": response.status_code,
            "reason": "Groww historical candles request for the exact NSE F&O option contract did not succeed.",
            "contract": contract,
            "request_window": {"start": start.isoformat(), "end": end.isoformat(), "interval": interval},
            "upstream_preview": body_preview,
        }

    body = response.json()
    payload = body.get("payload", body) if isinstance(body, dict) else {}
    candles = payload.get("candles", []) if isinstance(payload, dict) else []
    valid = isinstance(candles, list) and len(candles) > 0
    sample = candles[-3:] if valid else []
    return {
        "supported": bool(valid),
        "stage": "HISTORICAL_CANDLES",
        "http_status": response.status_code,
        "reason": "Historical option candles are available for this contract." if valid else "Groww accepted the request but returned no option candles for this window.",
        "contract": contract,
        "request_window": {"start": start.isoformat(), "end": end.isoformat(), "interval": interval},
        "candle_count": len(candles) if isinstance(candles, list) else 0,
        "sample_candles": sample,
        "backtest_implication": "TRUE_OPTION_PREMIUM_REPLAY_POSSIBLE" if valid else "NOT_YET_PROVEN",
    }
