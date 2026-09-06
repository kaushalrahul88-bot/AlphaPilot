"""Read-only Groww F&O history coverage probe for currently active expiries.

The probe is diagnostic only. It discovers contracts Groww exposes for one exact
expiry and samples near-ATM CE/PE (plus FUT when present) to find when daily and
5-minute candles actually begin. It never places orders, changes strategy policy,
or treats today's contract list as historically point-in-time.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx

from .fno_15m_candle_checkpoint_v2 import _is_auth_error, _refresh_after_401

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
_CONTRACT_RE = re.compile(r"-(?P<strike>[0-9]+(?:\.[0-9]+)?)-(?P<type>CE|PE)$", re.I)


def _payload(body: Any) -> dict[str, Any]:
    current = body if isinstance(body, Mapping) else {}
    child = current.get("payload") if isinstance(current, Mapping) else None
    if isinstance(child, Mapping):
        current = child
    return dict(current) if isinstance(current, Mapping) else {}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _stamp(value: Any) -> datetime | None:
    """Accept Groww's documented string timestamps and defensive epoch variants."""
    try:
        text = str(value).strip()
        if isinstance(value, (int, float)) or text.replace(".", "", 1).isdigit():
            number = float(value)
            if number > 1e12:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=UTC).astimezone(IST)
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            result = result.replace(tzinfo=IST)
        return result.astimezone(IST)
    except Exception:
        return None


async def _request(provider, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """One throttled Groww GET with the same fail-closed 401 refresh used by replay."""
    async def call() -> httpx.Response:
        throttle = getattr(provider, "_throttle", None)
        if callable(throttle):
            await throttle()
        async with httpx.AsyncClient(timeout=40) as client:
            return await client.get(
                f"{provider.BASE_URL}{path}",
                headers=await provider._headers(),
                params=dict(params),
            )

    response = await call()
    if response.status_code == 429:
        register = getattr(provider, "_register_rate_limit", None)
        if callable(register):
            await register()
    if response.status_code == 401:
        try:
            await _refresh_after_401(provider)
        except Exception:
            response.raise_for_status()
        response = await call()
    if response.status_code == 429:
        register = getattr(provider, "_register_rate_limit", None)
        if callable(register):
            await register()
    try:
        response.raise_for_status()
    except Exception as exc:
        if _is_auth_error(exc):
            raise RuntimeError("GROWW_CURRENT_EXPIRY_PROBE_AUTH_FAILED") from exc
        raise
    body = response.json()
    return body if isinstance(body, dict) else {}


async def _spot(provider, underlying: str) -> float | None:
    try:
        exchange, segment, trading_symbol, _ = provider._instrument(underlying)
        body = await _request(
            provider,
            "/v1/live-data/quote",
            {
                "exchange": exchange,
                "segment": segment,
                "trading_symbol": trading_symbol,
            },
        )
        p = _payload(body)
        for key in ("ltp", "last_price", "last_traded_price", "close"):
            value = _number(p.get(key))
            if value and value > 0:
                return value
        data = p.get("data")
        if isinstance(data, Mapping):
            for key in ("ltp", "last_price", "last_traded_price", "close"):
                value = _number(data.get(key))
                if value and value > 0:
                    return value
    except Exception:
        return None
    return None


def _contract_rows(contracts: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in contracts:
        text = str(symbol or "").strip()
        if not text:
            continue
        upper = text.upper()
        if upper.endswith("-FUT"):
            rows.append({"groww_symbol": text, "type": "FUT", "strike": None})
            continue
        match = _CONTRACT_RE.search(upper)
        if match:
            rows.append({
                "groww_symbol": text,
                "type": match.group("type").upper(),
                "strike": float(match.group("strike")),
            })
    return rows


def representative_contracts(rows: list[dict[str, Any]], spot: float | None) -> list[dict[str, Any]]:
    """Choose exact near-ATM CE/PE and FUT without pretending this is a full chain."""
    options = [row for row in rows if row["type"] in {"CE", "PE"} and row.get("strike") is not None]
    strikes = sorted({float(row["strike"]) for row in options})
    if not strikes:
        selected: list[dict[str, Any]] = []
    else:
        reference = spot if spot and spot > 0 else strikes[len(strikes) // 2]
        atm = min(strikes, key=lambda value: (abs(value - reference), value))
        selected = [row for row in options if float(row["strike"]) == atm and row["type"] in {"CE", "PE"}]
        selected.sort(key=lambda row: row["type"])
    futures = sorted(
        [row for row in rows if row["type"] == "FUT"],
        key=lambda row: row["groww_symbol"],
    )
    if futures:
        selected.append(futures[0])
    return selected


async def _candles(
    provider,
    groww_symbol: str,
    *,
    start: datetime,
    end: datetime,
    interval: str,
) -> list[list]:
    body = await _request(
        provider,
        "/v1/historical/candles",
        {
            "exchange": "NSE",
            "segment": "FNO",
            "groww_symbol": groww_symbol,
            "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
            "candle_interval": interval,
        },
    )
    return list(_payload(body).get("candles") or [])


def _bounds(candles: list[list]) -> tuple[str | None, str | None]:
    stamps = []
    for row in candles:
        if isinstance(row, (list, tuple)) and row:
            stamp = _stamp(row[0])
            if stamp is not None:
                stamps.append(stamp)
    if not stamps:
        return None, None
    return min(stamps).isoformat(), max(stamps).isoformat()


async def probe_current_expiry_history(
    provider,
    *,
    underlying: str,
    expiry_date: str,
    daily_search_start: str = "2025-01-01",
) -> dict[str, Any]:
    underlying = str(underlying or "").upper().strip()
    expiry_date = str(expiry_date or "").strip()
    if not underlying or not expiry_date:
        raise ValueError("underlying and expiry_date are required")

    contracts_body = await _request(
        provider,
        "/v1/historical/contracts",
        {
            "exchange": "NSE",
            "underlying_symbol": underlying,
            "expiry_date": expiry_date,
        },
    )
    contracts = [str(item) for item in (_payload(contracts_body).get("contracts") or []) if str(item).strip()]
    rows = _contract_rows(contracts)
    spot = await _spot(provider, underlying)
    sampled = representative_contracts(rows, spot)

    search_start = datetime.fromisoformat(daily_search_start).replace(tzinfo=IST)
    now = datetime.now(IST)
    end = min(now, datetime.fromisoformat(expiry_date).replace(hour=23, minute=59, tzinfo=IST))
    sample_results: list[dict[str, Any]] = []
    for item in sampled:
        symbol = str(item["groww_symbol"])
        daily_error = None
        try:
            daily = await _candles(provider, symbol, start=search_start, end=end, interval="1day")
        except Exception as exc:
            daily = []
            daily_error = f"{exc.__class__.__name__}: {str(exc)[:320]}"
        daily_first, daily_last = _bounds(daily)

        intraday: list[list] = []
        intraday_error = None
        if daily_first:
            first_day = datetime.fromisoformat(daily_first).astimezone(IST).replace(hour=9, minute=15, second=0, microsecond=0)
            first_window_end = min(end, first_day + timedelta(days=10))
            try:
                intraday = await _candles(
                    provider,
                    symbol,
                    start=first_day,
                    end=first_window_end,
                    interval="5minute",
                )
            except Exception as exc:
                intraday_error = f"{exc.__class__.__name__}: {str(exc)[:320]}"
        intraday_first, intraday_last = _bounds(intraday)
        sample_results.append({
            **item,
            "daily_candles": len(daily),
            "daily_first": daily_first,
            "daily_last": daily_last,
            "daily_error": daily_error,
            "first_window_5m_candles": len(intraday),
            "first_5m": intraday_first,
            "first_window_5m_last": intraday_last,
            "intraday_error": intraday_error,
        })

    proven_samples = [
        item for item in sample_results
        if item.get("daily_first") and item.get("first_5m")
        and not item.get("daily_error") and not item.get("intraday_error")
    ]
    coverage_proven = bool(contracts and sampled and proven_samples)
    status = "COMPLETED" if coverage_proven else "COVERAGE_UNPROVEN"

    return {
        "status": status,
        "coverage_proven": coverage_proven,
        "underlying": underlying,
        "expiry_date": expiry_date,
        "spot_reference": spot,
        "contract_counts": {
            "total": len(contracts),
            "CE": sum(row["type"] == "CE" for row in rows),
            "PE": sum(row["type"] == "PE" for row in rows),
            "FUT": sum(row["type"] == "FUT" for row in rows),
        },
        "sample_basis": "near-ATM CE/PE at latest quote plus one FUT when exposed",
        "sampled_contracts": sample_results,
        "proven_sample_count": len(proven_samples),
        "important_limit": (
            "Current contract repository is not historical point-in-time chain state. "
            "A rolling-expiry backtest must admit a contract at a historical click only "
            "if its own candle/OI tape already exists by that click."
        ),
        "live_execution": False,
        "capital_committed": 0,
        "strategy_policy_changed": False,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_CURRENT_EXPIRY_HISTORY_PROBE_V1_HARDENED",
        "read_only": True,
        "groww_contract_repository": True,
        "historical_daily_probe": True,
        "historical_5m_first_window_probe": True,
        "historical_fno_oi_available_in_candle_rows": True,
        "coverage_must_be_proven_non_empty": True,
        "point_in_time_chain_reconstructed": False,
        "live_execution": False,
        "capital_committed": 0,
    }
