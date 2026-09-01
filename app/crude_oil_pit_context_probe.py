from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

IST = timezone(timedelta(hours=5, minutes=30))

CONTEXT_SPECS = {
    "WTI_CRUDE": {"ticker": "CL=F", "bar_minutes": 60},
    "BRENT_CRUDE": {"ticker": "BZ=F", "bar_minutes": 60},
    "USDINR": {"ticker": "INR=X", "bar_minutes": 60},
    "DXY": {"ticker": "DX-Y.NYB", "bar_minutes": 60},
}


def _parse_window(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    return stamp.astimezone(timezone.utc)


def _normalize_chart(result: dict, *, bar_minutes: int) -> list[dict]:
    timestamps = list(result.get("timestamp") or [])
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = list(quotes.get("open") or [])
    highs = list(quotes.get("high") or [])
    lows = list(quotes.get("low") or [])
    closes = list(quotes.get("close") or [])
    volumes = list(quotes.get("volume") or [])
    rows = []
    for i, epoch in enumerate(timestamps):
        values = [
            opens[i] if i < len(opens) else None,
            highs[i] if i < len(highs) else None,
            lows[i] if i < len(lows) else None,
            closes[i] if i < len(closes) else None,
        ]
        if any(value is None for value in values):
            continue
        start = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        available = start + timedelta(minutes=bar_minutes)
        rows.append({
            "bar_start": start.astimezone(IST).isoformat(),
            "available_at": available.astimezone(IST).isoformat(),
            "open": float(values[0]),
            "high": float(values[1]),
            "low": float(values[2]),
            "close": float(values[3]),
            "volume": float(volumes[i]) if i < len(volumes) and volumes[i] is not None else None,
        })
    rows.sort(key=lambda row: row["bar_start"])
    deduped = []
    seen = set()
    for row in rows:
        if row["bar_start"] in seen:
            continue
        seen.add(row["bar_start"])
        deduped.append(row)
    return deduped


async def _fetch_one(client: httpx.AsyncClient, series: str, spec: dict, start: datetime, end: datetime) -> tuple[str, dict]:
    ticker = spec["ticker"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
    params = {
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "1h",
        "includePrePost": "true",
        "events": "div,splits",
    }
    try:
        response = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0 AlphaPilot/1.0"})
        response.raise_for_status()
        chart = response.json().get("chart") or {}
        if chart.get("error"):
            raise RuntimeError(str(chart["error"]))
        result = (chart.get("result") or [None])[0]
        if not result:
            raise RuntimeError("empty chart result")
        rows = _normalize_chart(result, bar_minutes=int(spec["bar_minutes"]))
        if not rows:
            raise RuntimeError("no complete OHLC rows")
        first = datetime.fromisoformat(rows[0]["bar_start"])
        last = datetime.fromisoformat(rows[-1]["bar_start"])
        requested_start_ist = start.astimezone(IST)
        requested_end_ist = end.astimezone(IST)
        return series, {
            "status": "AVAILABLE",
            "ticker": ticker,
            "source": "Yahoo Finance public chart",
            "research_only": True,
            "bar_minutes": int(spec["bar_minutes"]),
            "bar_visibility": f"BAR_START_PLUS_{int(spec['bar_minutes'])}_MINUTES",
            "rows": len(rows),
            "first_bar_start": rows[0]["bar_start"],
            "last_bar_start": rows[-1]["bar_start"],
            "covers_requested_start_date": first.date() <= requested_start_ist.date(),
            "covers_requested_end_date": last.date() >= (requested_end_ist - timedelta(days=1)).date(),
            "data": rows,
        }
    except Exception as exc:
        return series, {
            "status": "UNAVAILABLE",
            "ticker": ticker,
            "source": "Yahoo Finance public chart",
            "research_only": True,
            "error": str(exc) or exc.__class__.__name__,
            "data": [],
        }


async def probe_crude_oil_pit_context(
    start: datetime | str = "2026-06-01T00:00:00+05:30",
    end: datetime | str = "2026-09-01T00:00:00+05:30",
) -> dict:
    start_utc = _parse_window(start)
    end_utc = _parse_window(end)
    if end_utc <= start_utc:
        raise ValueError("end must be after start")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        pairs = await asyncio.gather(*[
            _fetch_one(client, series, spec, start_utc, end_utc)
            for series, spec in CONTEXT_SPECS.items()
        ])
    feeds = dict(pairs)
    ready = [
        series for series, row in feeds.items()
        if row.get("status") == "AVAILABLE"
        and row.get("covers_requested_start_date")
        and row.get("covers_requested_end_date")
    ]
    return {
        "mode": "CRUDE_OIL_PIT_CONTEXT_HISTORICAL_PROBE_V1",
        "research_only": True,
        "decision_effect": "NONE",
        "requested_start": start_utc.astimezone(IST).isoformat(),
        "requested_end_exclusive": end_utc.astimezone(IST).isoformat(),
        "feeds": feeds,
        "full_window_hourly_candidates": sorted(ready),
        "policy": {
            "hourly_bar_visible_only_after_completion": True,
            "continuous_global_future_is_context_not_crude_oil_mini_substitute": True,
            "no_directional_votes_created": True,
            "provider_must_be_frozen_before_ablation": True,
        },
    }


def probe_crude_oil_pit_context_sync(start=None, end=None) -> dict:
    kwargs = {}
    if start is not None:
        kwargs["start"] = start
    if end is not None:
        kwargs["end"] = end
    return asyncio.run(probe_crude_oil_pit_context(**kwargs))
