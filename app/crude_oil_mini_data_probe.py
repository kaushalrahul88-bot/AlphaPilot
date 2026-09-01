from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from .commodity_backtest import _fetch_chunked, _ts
from .commodity_option_history import fetch_mcx_option_day
from .crude_oil_mini_contracts import (
    CRUDE_OIL_MINI,
    audit_current_crude_oil_mini_universe,
    fetch_crude_oil_mini_master,
)

IST = ZoneInfo("Asia/Kolkata")


def _complete_sessions(candles: list[list]) -> list[dict]:
    by_day: dict[date, list[list]] = defaultdict(list)
    for row in candles or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = _ts(row[0]).astimezone(IST)
        except Exception:
            continue
        by_day[stamp.date()].append(row)

    sessions = []
    for day, rows in sorted(by_day.items()):
        ordered = sorted(rows, key=lambda item: _ts(item[0]))
        first = _ts(ordered[0][0]).astimezone(IST)
        last = _ts(ordered[-1][0]).astimezone(IST)
        # MCX evening close can vary seasonally.  For a research click-day, require
        # enough bars plus late-session coverage; do not infer completeness from
        # weekday alone.
        complete = len(ordered) >= 140 and last.hour >= 21
        sessions.append({
            "date": day.isoformat(),
            "candles": len(ordered),
            "first_at": first.isoformat(),
            "last_at": last.isoformat(),
            "complete_for_20_click_research": complete,
        })
    return sessions


def _close_near_mid_session(candles: list[list], day: date) -> float | None:
    rows = []
    for row in candles or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        stamp = _ts(row[0]).astimezone(IST)
        if stamp.date() == day and stamp.hour <= 18:
            rows.append((stamp, row))
    if not rows:
        return None
    _, selected = min(rows, key=lambda item: abs((item[0].hour * 60 + item[0].minute) - 18 * 60))
    try:
        return float(selected[4])
    except (TypeError, ValueError):
        return None


def _nearest_option(options: list[dict], *, expiry: str, option_type: str, price: float) -> dict | None:
    candidates = [
        row for row in options
        if row.get("underlying") == CRUDE_OIL_MINI
        and row.get("instrument_type") == option_type
        and row.get("expiry") == expiry
        and row.get("strike") is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: (abs(float(row["strike"]) - price), float(row["strike"])))


def _sample_days(complete_sessions: list[dict]) -> list[date]:
    days = [date.fromisoformat(row["date"]) for row in complete_sessions if row["complete_for_20_click_research"]]
    if not days:
        return []
    indexes = sorted({0, len(days) // 2, len(days) - 1})
    return [days[index] for index in indexes]


async def probe_current_crude_oil_mini_history(
    provider,
    *,
    start: datetime,
    end: datetime,
) -> dict:
    """Probe only the currently listed CRUDEOILM future and Mini options.

    No previous futures contract is discovered or stitched.  The function answers
    one question: how much usable history exists for the current Mini contract and
    whether real Mini option-premium candles are available on representative days.
    """
    start_at, end_at = _ts(start), _ts(end)
    universe = await audit_current_crude_oil_mini_universe(end_at)
    future = dict(universe["future"])
    candles = await _fetch_chunked(provider, future, 5, start_at, end_at)
    sessions = _complete_sessions(candles)
    complete = [row for row in sessions if row["complete_for_20_click_research"]]

    master = await fetch_crude_oil_mini_master()
    option_expiry = universe["nearest_option_expiry"]
    option_probes = []
    for day in _sample_days(complete):
        reference = _close_near_mid_session(candles, day)
        if reference is None:
            continue
        for option_type in ("CE", "PE"):
            contract = _nearest_option(
                master,
                expiry=option_expiry,
                option_type=option_type,
                price=reference,
            )
            if contract is None:
                option_probes.append({
                    "date": day.isoformat(),
                    "option_type": option_type,
                    "status": "CONTRACT_NOT_FOUND",
                })
                continue
            try:
                history = await fetch_mcx_option_day(provider, contract, day)
                rows = history.get("candles") or []
                option_probes.append({
                    "date": day.isoformat(),
                    "option_type": option_type,
                    "status": history.get("status"),
                    "trading_symbol": contract.get("trading_symbol"),
                    "groww_symbol": contract.get("groww_symbol"),
                    "expiry": contract.get("expiry"),
                    "strike": contract.get("strike"),
                    "lot_size": contract.get("lot_size"),
                    "reference_underlying": round(reference, 4),
                    "candles": len(rows),
                    "first_at": rows[0][0] if rows else None,
                    "last_at": rows[-1][0] if rows else None,
                })
            except Exception as exc:
                option_probes.append({
                    "date": day.isoformat(),
                    "option_type": option_type,
                    "status": "UPSTREAM_ERROR",
                    "trading_symbol": contract.get("trading_symbol"),
                    "expiry": contract.get("expiry"),
                    "strike": contract.get("strike"),
                    "lot_size": contract.get("lot_size"),
                    "reference_underlying": round(reference, 4),
                    "error": f"{exc.__class__.__name__}: {str(exc)[:300]}",
                })

    counts = [row["candles"] for row in complete]
    option_available = [row for row in option_probes if int(row.get("candles") or 0) > 0]
    return {
        "mode": "CRUDE_OIL_MINI_CURRENT_CONTRACT_DATA_PROBE_V1",
        "product": "CRUDE_OIL_MINI",
        "underlying_symbol": CRUDE_OIL_MINI,
        "research_only": True,
        "news_enabled": False,
        "live_execution_enabled": False,
        "older_futures_contracts_used": False,
        "requested_window": {"start": start_at.isoformat(), "end": end_at.isoformat()},
        "future": future,
        "future_candles": len(candles),
        "future_first_at": _ts(candles[0][0]).isoformat() if candles else None,
        "future_last_at": _ts(candles[-1][0]).isoformat() if candles else None,
        "sessions": sessions,
        "complete_sessions": len(complete),
        "complete_session_first": complete[0]["date"] if complete else None,
        "complete_session_last": complete[-1]["date"] if complete else None,
        "median_complete_session_candles": median(counts) if counts else None,
        "option_expiry_probed": option_expiry,
        "option_probes": option_probes,
        "option_premium_history_found": bool(option_available),
        "option_probe_successes": len(option_available),
        "option_probe_attempts": len(option_probes),
        "ready_for_no_news_brain_replay": len(complete) >= 5,
        "ready_for_option_premium_scoring": bool(option_available),
        "integrity": {
            "current_mini_future_only": True,
            "regular_crude_used": False,
            "synthetic_option_prices_used": False,
            "future_expiry": universe["future_expiry"],
            "option_expiry": option_expiry,
            "future_and_option_expiry_independent": True,
        },
    }
