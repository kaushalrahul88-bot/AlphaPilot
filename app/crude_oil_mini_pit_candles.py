from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_candle_collector import PostgresCandleStore, _records
from .commodity_backtest import _fetch_chunked, _ts
from .commodity_mtf import completed_rows
from .crude_oil_mini_contracts import fetch_crude_oil_mini_master, resolve_crude_oil_mini_universe

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "CRUDEOILM"
TIMEFRAME_MINUTES = 5


async def resolve_current_crude_oil_mini_future(now: datetime | None = None) -> dict:
    observed = _ts(now or datetime.now(IST))
    rows = await fetch_crude_oil_mini_master()
    universe = resolve_crude_oil_mini_universe(rows, observed)
    future = dict(universe["future"])
    return {
        "exchange": future.get("exchange") or "MCX",
        "segment": future.get("segment") or "COMMODITY",
        "trading_symbol": future["trading_symbol"],
        "groww_symbol": future.get("groww_symbol"),
        "expiry_date": future.get("expiry"),
    }


async def collect_crude_oil_mini_pit_candles(provider, store: PostgresCandleStore, now: datetime | None = None) -> dict:
    """Collect only the small live overlap needed by CRUDEOILM Current Mind.

    This deliberately does not use the generic live /v1/candles/CRUDEOILM path,
    whose historical lookback is research-oriented. Historical bootstrap remains a
    separate operation.
    """
    collected_at = _ts(now or datetime.now(IST))
    await store.initialize()
    contract = await resolve_current_crude_oil_mini_future(collected_at)
    latest = await store.latest_candle_at(contract["trading_symbol"], TIMEFRAME_MINUTES)
    fetch_start = (_ts(latest) - timedelta(minutes=10)) if latest else (collected_at - timedelta(hours=6))
    fetched = await _fetch_chunked(provider, contract, TIMEFRAME_MINUTES, fetch_start, collected_at)
    completed = completed_rows(fetched, collected_at, TIMEFRAME_MINUTES)
    records = _records(SYMBOL, contract, TIMEFRAME_MINUTES, completed, collected_at)
    upserted = await store.upsert(records)
    return {
        "status": "COLLECTED",
        "symbol": SYMBOL,
        "contract": contract["trading_symbol"],
        "timeframe_minutes": TIMEFRAME_MINUTES,
        "fetch_start": fetch_start.isoformat(),
        "collected_at": collected_at.isoformat(),
        "fetched": len(fetched),
        "completed": len(completed),
        "upserted": upserted,
        "latest_completed_at": records[-1]["candle_at"].isoformat() if records else None,
        "regular_crude_alias_allowed": False,
    }


async def read_crude_oil_mini_pit_candles(store: PostgresCandleStore, as_of: datetime, lookback_days: int = 7) -> list[list]:
    observed = _ts(as_of)
    return await store.read_symbol_pit(
        SYMBOL,
        TIMEFRAME_MINUTES,
        observed - timedelta(days=max(1, int(lookback_days))),
        observed,
        observed,
    )
