from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_backtest import _fetch_chunked, _ts
from .commodity_candle_collector import PROVIDER, PostgresCandleStore, _records
from .commodity_mtf import completed_rows
from .crude_oil_mini_contracts import (
    fetch_crude_oil_mini_master,
    resolve_crude_oil_mini_universe,
)

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "CRUDEOILM"
TIMEFRAME_MINUTES = 5

CRUDE_MINI_PIT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS commodity_candles_crude_mini_pit_idx
    ON commodity_candles (symbol, timeframe_minutes, collected_at, candle_at DESC);
"""

CRUDE_MINI_PIT_UPSERT_SQL = """
INSERT INTO commodity_candles (
    provider, symbol, exchange, segment, trading_symbol, groww_symbol,
    expiry_date, timeframe_minutes, candle_at, open, high, low, close,
    volume, open_interest, collected_at
) VALUES (
    %(provider)s, %(symbol)s, %(exchange)s, %(segment)s, %(trading_symbol)s,
    %(groww_symbol)s, %(expiry_date)s, %(timeframe_minutes)s, %(candle_at)s,
    %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s,
    %(open_interest)s, %(collected_at)s
)
ON CONFLICT (provider, trading_symbol, timeframe_minutes, candle_at)
DO UPDATE SET
    collected_at = LEAST(commodity_candles.collected_at, EXCLUDED.collected_at);
"""


class CrudeOilMiniPITStore(PostgresCandleStore):
    """CRUDEOILM-only store preserving first-seen candle state and availability.

    The generic commodity store is intentionally left untouched because it is shared
    with older Copper/CRUDEOIL/NATURALGAS research. This subclass changes only the
    CRUDEOILM live/PIT path.

    Existing OHLCV/OI values are not rewritten on refresh. If Groww revises a candle
    later, using the revised values with the original availability timestamp would
    leak future information into a historical PIT replay. A future revision-aware
    store can retain both versions explicitly; this first implementation stays
    strictly first-seen.
    """

    def _initialize_sync(self):
        super()._initialize_sync()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(CRUDE_MINI_PIT_INDEX_SQL)

    def _upsert_sync(self, records):
        if not records:
            return 0
        if any(str(record.get("symbol") or "").upper() != SYMBOL for record in records):
            raise ValueError("CrudeOilMiniPITStore accepts CRUDEOILM records only")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(CRUDE_MINI_PIT_UPSERT_SQL, records)
        return len(records)

    def _read_symbol_pit_sync(
        self,
        symbol: str,
        timeframe_minutes: int,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[list]:
        normalized = str(symbol).upper().strip()
        if normalized != SYMBOL:
            raise ValueError("CrudeOilMiniPITStore reads CRUDEOILM only")
        interval = int(timeframe_minutes)
        sql = """
            SELECT candle_at, open, high, low, close, volume, open_interest
            FROM commodity_candles
            WHERE provider = %s
              AND symbol = %s
              AND timeframe_minutes = %s
              AND candle_at >= %s
              AND candle_at <= %s
              AND candle_at + (%s * INTERVAL '1 minute') <= %s
              AND collected_at <= %s
            ORDER BY candle_at ASC
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    (
                        PROVIDER,
                        SYMBOL,
                        interval,
                        start,
                        end,
                        interval,
                        as_of,
                        as_of,
                    ),
                )
                rows = cursor.fetchall()
        return [
            [
                candle_at.isoformat(),
                float(open_price),
                float(high),
                float(low),
                float(close),
                float(volume or 0),
                float(open_interest) if open_interest is not None else None,
            ]
            for candle_at, open_price, high, low, close, volume, open_interest in rows
        ]

    async def read_symbol_pit(
        self,
        symbol: str,
        timeframe_minutes: int,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[list]:
        return await asyncio.to_thread(
            self._read_symbol_pit_sync,
            symbol,
            timeframe_minutes,
            start,
            end,
            as_of,
        )


async def resolve_current_crude_oil_mini_future(
    now: datetime | None = None,
) -> dict:
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


async def collect_crude_oil_mini_pit_candles(
    provider,
    store: CrudeOilMiniPITStore,
    now: datetime | None = None,
) -> dict:
    """Collect only the bounded live overlap needed by CRUDEOILM Current Mind.

    No regular CRUDEOIL alias is accepted. The expensive research/history path is
    deliberately not used here.
    """
    collected_at = _ts(now or datetime.now(IST))
    await store.initialize()
    contract = await resolve_current_crude_oil_mini_future(collected_at)
    latest = await store.latest_candle_at(
        contract["trading_symbol"], TIMEFRAME_MINUTES
    )
    fetch_start = (
        _ts(latest) - timedelta(minutes=10)
        if latest
        else collected_at - timedelta(hours=6)
    )
    fetched = await _fetch_chunked(
        provider,
        contract,
        TIMEFRAME_MINUTES,
        fetch_start,
        collected_at,
    )
    completed = completed_rows(fetched, collected_at, TIMEFRAME_MINUTES)
    records = _records(
        SYMBOL,
        contract,
        TIMEFRAME_MINUTES,
        completed,
        collected_at,
    )
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
        "latest_completed_at": (
            records[-1]["candle_at"].isoformat() if records else None
        ),
        "pit_provenance": "FIRST_SEEN_CANDLE_STATE_IMMUTABLE",
        "regular_crude_alias_allowed": False,
    }


async def read_crude_oil_mini_pit_candles(
    store: CrudeOilMiniPITStore,
    as_of: datetime,
    lookback_days: int = 7,
) -> list[list]:
    observed = _ts(as_of)
    return await store.read_symbol_pit(
        SYMBOL,
        TIMEFRAME_MINUTES,
        observed - timedelta(days=max(1, int(lookback_days))),
        observed,
        observed,
    )
