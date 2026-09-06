"""Read-only Groww history coverage probe for currently active OPTION expiries.

This diagnostic is intentionally options-only. It discovers exact CE/PE contracts
Groww exposes for one underlying/expiry, selects the current near-ATM CE and PE,
and proves their available daily and 5-minute historical tape. Futures are never
sampled or mixed into this workflow.

The current contract repository is NOT treated as historical point-in-time chain
state. A later backtest may admit an exact contract at a historical click only if
that contract's own tape already existed at that timestamp.
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
SEARCH_DAYS = 179  # strictly below Groww's documented 180-day 1-day limit
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
    """One throttled Groww GET with fail-closed 401 refresh semantics."""
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
            {"exchange": exchange, "segment": segment, "trading_symbol": trading_symbol},
        )
        payload = _payload(body)
        candidates = [payload]
        if isinstance(payload.get("data"), Mapping):
            candidates.append(payload["data"])
        for row in candidates:
            for key in ("ltp", "last_price", "last_traded_price", "close"):
                value = _number(row.get(key))
                if value and value > 0:
                    return value
    except Exception:
        return None
    return None


def _contract_rows(contracts: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in contracts:
        text = str(symbol or "").strip()
        match = _CONTRACT_RE.search(text.upper()) if text else None
        if not match:
            continue
        rows.append({
            "groww_symbol": text,
            "type": match.group("type").upper(),
            "strike": float(match.group("strike")),
        })
    return rows


def representative_contracts(rows: list[dict[str, Any]], spot: float | None) -> list[dict[str, Any]]:
    """Return exact near-ATM CE and PE only; Futures are intentionally excluded."""
    strikes = sorted({float(row["strike"]) for row in rows if row.get("strike") is not None})
    if not strikes:
        return []
    reference = spot if spot and spot > 0 else strikes[len(strikes) // 2]
    atm = min(strikes, key=lambda value: (abs(value - reference), value))
    selected = [
        row for row in rows
        if float(row["strike"]) == atm and row.get("type") in {"CE", "PE"}
    ]
    selected.sort(key=lambda row: row["type"])
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
) -> dict[str, Any]:
    underlying = str(underlying or "").upper().strip()
    expiry_date = str(expiry_date or "").strip()
    if not underlying or not expiry_date:
        raise ValueError("underlying and expiry_date are required")

    contracts_body = await _request(
        provider,
        "/v1/historical/contracts",
        {"exchange": "NSE", "underlying_symbol": underlying, "expiry_date": expiry_date},
    )
    contracts = [
        str(item) for item in (_payload(contracts_body).get("contracts") or [])
        if str(item).strip()
    ]
    rows = _contract_rows(contracts)
    spot = await _spot(provider, underlying)
    sampled = representative_contracts(rows, spot)

    now = datetime.now(IST)
    expiry_end = datetime.fromisoformat(expiry_date).replace(hour=23, minute=59, tzinfo=IST)
    end = min(now, expiry_end)
    search_start = end - timedelta(days=SEARCH_DAYS)

    sample_results: list[dict[str, Any]] = []
    for item in sampled:
        symbol = str(item["groww_symbol"])
        daily_error = None
        try:
            daily = await _candles(
                provider,
                symbol,
                start=search_start,
                end=end,
                interval="1day",
            )
        except Exception as exc:
            daily = []
            daily_error = f"{exc.__class__.__name__}: {str(exc)[:320]}"
        daily_first, daily_last = _bounds(daily)

        intraday: list[list] = []
        intraday_error = None
        if daily_first:
            first_day = datetime.fromisoformat(daily_first).astimezone(IST).replace(
                hour=9, minute=15, second=0, microsecond=0
            )
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

        boundary_hit = False
        if daily_first:
            first_stamp = datetime.fromisoformat(daily_first).astimezone(IST)
            boundary_hit = first_stamp <= search_start + timedelta(days=1)

        sample_results.append({
            **item,
            "search_start": search_start.isoformat(),
            "search_end": end.isoformat(),
            "daily_candles": len(daily),
            "daily_first": daily_first,
            "daily_last": daily_last,
            "daily_error": daily_error,
            "earliest_may_predate_search_window": boundary_hit,
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

    return {
        "status": "COMPLETED" if coverage_proven else "COVERAGE_UNPROVEN",
        "coverage_proven": coverage_proven,
        "underlying": underlying,
        "expiry_date": expiry_date,
        "spot_reference": spot,
        "contract_counts": {
            "raw_total": len(contracts),
            "option_contracts": len(rows),
            "CE": sum(row["type"] == "CE" for row in rows),
            "PE": sum(row["type"] == "PE" for row in rows),
        },
        "sample_basis": "current near-ATM CE and PE only",
        "sampled_contracts": sample_results,
        "proven_sample_count": len(proven_samples),
        "groww_request_limits_respected": {
            "daily_search_days": SEARCH_DAYS,
            "daily_documented_max_days": 180,
            "five_minute_first_window_days": 10,
            "five_minute_documented_max_days": 30,
        },
        "important_limit": (
            "Current contract repository is not historical point-in-time chain state. "
            "A historical replay may admit an exact option only when its own tape "
            "already existed by the simulated click."
        ),
        "futures_sampled": False,
        "live_execution": False,
        "capital_committed": 0,
        "strategy_policy_changed": False,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_CURRENT_EXPIRY_OPTION_HISTORY_PROBE_V2",
        "read_only": True,
        "options_only": True,
        "futures_sampled": False,
        "groww_contract_repository": True,
        "historical_daily_probe": True,
        "historical_5m_first_window_probe": True,
        "historical_fno_oi_available_in_candle_rows": True,
        "daily_search_below_180_day_limit": True,
        "five_minute_search_below_30_day_limit": True,
        "coverage_must_be_proven_non_empty": True,
        "point_in_time_chain_reconstructed": False,
        "live_execution": False,
        "capital_committed": 0,
    }
