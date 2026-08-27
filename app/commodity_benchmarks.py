from __future__ import annotations

from datetime import datetime
from statistics import mean
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx


IST = ZoneInfo("Asia/Kolkata")
BENCHMARK_SPECS = {
    "CRUDEOIL": {"symbol": "WTI", "ticker": "CL=F"},
    "NATURALGAS": {"symbol": "HENRY_HUB", "ticker": "NG=F"},\n    "COPPER": {"symbol": "COMEX_COPPER", "ticker": "HG=F"},
}
MOMENTUM_BARS = 6
MOMENTUM_MIN_PCT = 0.10
FRESHNESS_MINUTES = 15


def _timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or str(value).isdigit():
        parsed = datetime.fromtimestamp(float(value), IST)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _number(value):
    try:
        output = float(value)
        return output if output > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_chart(payload):
    chart = payload.get("chart", {}) if isinstance(payload, dict) else {}
    if chart.get("error"):
        raise ValueError(f"Yahoo chart error: {chart['error']}")
    result = (chart.get("result") or [None])[0]
    if not isinstance(result, dict):
        raise ValueError("Yahoo chart returned no result")
    timestamps = result.get("timestamp") or []
    quote_rows = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote_rows.get("open") or []
    highs = quote_rows.get("high") or []
    lows = quote_rows.get("low") or []
    closes = quote_rows.get("close") or []
    volumes = quote_rows.get("volume") or []
    rows = []
    for index, raw_timestamp in enumerate(timestamps):
        values = [series[index] if index < len(series) else None for series in (opens, highs, lows, closes)]
        opened, high, low, close = [_number(value) for value in values]
        if None in {opened, high, low, close} or high < low:
            continue
        volume = volumes[index] if index < len(volumes) else 0
        try:
            volume = max(0.0, float(volume or 0))
        except (TypeError, ValueError):
            volume = 0.0
        rows.append([_timestamp(raw_timestamp).isoformat(), opened, high, low, close, volume])
    return rows


async def fetch_benchmark_candles(symbol, start, end, client=None):
    normalized = str(symbol).upper().strip()
    if normalized not in BENCHMARK_SPECS:
        raise ValueError("symbol must be COPPER, CRUDEOIL or NATURALGAS")
    start_at = _timestamp(start)
    end_at = _timestamp(end)
    if end_at <= start_at:
        raise ValueError("end must be after start")
    ticker = BENCHMARK_SPECS[normalized]["ticker"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
    params = {
        "period1": int(start_at.timestamp()),
        "period2": int(end_at.timestamp()),
        "interval": "5m",
        "includePrePost": "true",
    }
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        response = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0 AlphaPilot/1.0"})
        response.raise_for_status()
        rows = _parse_chart(response.json())
    finally:
        if owns_client:
            await client.aclose()
    return {
        "status": "AVAILABLE" if rows else "UNAVAILABLE",
        "commodity": normalized,
        "benchmark_symbol": BENCHMARK_SPECS[normalized]["symbol"],
        "ticker": ticker,
        "source": "Yahoo Finance public chart",
        "research_only": True,
        "execution_grade": False,
        "candles": rows,
    }


def benchmark_confirmation(symbol, candles, click_at):
    normalized = str(symbol).upper().strip()
    if normalized not in BENCHMARK_SPECS:
        raise ValueError("symbol must be COPPER, CRUDEOIL or NATURALGAS")
    click = _timestamp(click_at)
    rows = []
    for row in candles or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = _timestamp(row[0])
        except Exception:
            continue
        if stamp > click or stamp.date() != click.date():
            continue
        opened, high, low, close = [_number(row[index]) for index in range(1, 5)]
        if None in {opened, high, low, close} or high < low:
            continue
        volume = float(row[5] or 0) if len(row) > 5 else 0.0
        rows.append([stamp, opened, high, low, close, max(0.0, volume)])
    rows.sort(key=lambda row: row[0])
    benchmark_symbol = BENCHMARK_SPECS[normalized]["symbol"]
    if len(rows) < MOMENTUM_BARS + 1:
        return {
            "symbol": benchmark_symbol,
            "direction": "NEUTRAL",
            "fresh": False,
            "as_of": rows[-1][0].isoformat() if rows else None,
            "status": "INSUFFICIENT_DATA",
            "no_future_data": True,
        }
    price_volume = sum(((row[2] + row[3] + row[4]) / 3.0) * row[5] for row in rows)
    total_volume = sum(row[5] for row in rows)
    session_vwap = price_volume / total_volume if total_volume > 0 else mean(row[4] for row in rows)
    last = rows[-1][4]
    momentum = (last / rows[-(MOMENTUM_BARS + 1)][4] - 1.0) * 100.0
    direction = (
        "BULLISH" if last > session_vwap and momentum >= MOMENTUM_MIN_PCT
        else "BEARISH" if last < session_vwap and momentum <= -MOMENTUM_MIN_PCT
        else "NEUTRAL"
    )
    age_minutes = (click - rows[-1][0]).total_seconds() / 60.0
    return {
        "symbol": benchmark_symbol,
        "direction": direction,
        "fresh": 0 <= age_minutes <= FRESHNESS_MINUTES,
        "as_of": rows[-1][0].isoformat(),
        "status": "AVAILABLE",
        "last_price": round(last, 4),
        "session_vwap": round(session_vwap, 4),
        "momentum_30m_pct": round(momentum, 3),
        "momentum_minimum_pct": MOMENTUM_MIN_PCT,
        "age_minutes": round(age_minutes, 1),
        "no_future_data": True,
        "research_only": True,
        "execution_grade": False,
    }
