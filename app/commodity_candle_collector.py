from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from .commodity_backtest import _fetch_chunked, _ts
from .commodity_mtf import TIMEFRAMES, completed_rows
from .commodities import resolve_nearest_mcx_future


IST = ZoneInfo("Asia/Kolkata")
SYMBOLS = ("COPPER", "CRUDEOIL", "NATURALGAS")
PROVIDER = "GROWW"
DEFAULT_LOOKBACK_DAYS = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS commodity_candles (
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    segment TEXT NOT NULL,
    trading_symbol TEXT NOT NULL,
    groww_symbol TEXT,
    expiry_date DATE,
    timeframe_minutes SMALLINT NOT NULL,
    candle_at TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC NOT NULL DEFAULT 0,
    open_interest NUMERIC,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, trading_symbol, timeframe_minutes, candle_at)
);
CREATE INDEX IF NOT EXISTS commodity_candles_symbol_time_idx
    ON commodity_candles (symbol, timeframe_minutes, candle_at DESC);
"""

UPSERT_SQL = """
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
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    open_interest = EXCLUDED.open_interest,
    collected_at = EXCLUDED.collected_at;
"""


class CandleStore(Protocol):
    async def initialize(self) -> None: ...
    async def latest_candle_at(self, trading_symbol: str, timeframe_minutes: int) -> datetime | None: ...
    async def upsert(self, records: list[dict]) -> int: ...
    async def read_symbol(self, symbol: str, timeframe_minutes: int, start: datetime, end: datetime) -> list[list]: ...
    async def read_symbol_contract_segments(self, symbol: str, timeframe_minutes: int, start: datetime, end: datetime) -> list[dict]: ...
    async def status(self) -> dict: ...


class PostgresCandleStore:
    """Durable PostgreSQL store; psycopg is imported only when configured."""

    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for commodity candle collection")

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url, connect_timeout=10)

    def _initialize_sync(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA_SQL)

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    def _latest_candle_at_sync(self, trading_symbol, timeframe_minutes):
        sql = """
            SELECT MAX(candle_at) FROM commodity_candles
            WHERE provider = %s AND trading_symbol = %s AND timeframe_minutes = %s
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (PROVIDER, trading_symbol, timeframe_minutes))
                row = cursor.fetchone()
        return row[0] if row else None

    async def latest_candle_at(self, trading_symbol, timeframe_minutes):
        return await asyncio.to_thread(
            self._latest_candle_at_sync, trading_symbol, timeframe_minutes,
        )

    def _upsert_sync(self, records):
        if not records:
            return 0
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(UPSERT_SQL, records)
        return len(records)

    async def upsert(self, records):
        return await asyncio.to_thread(self._upsert_sync, records)


    def _read_symbol_sync(self, symbol, timeframe_minutes, start, end):
        sql = """
            SELECT candle_at, open, high, low, close, volume, open_interest
            FROM commodity_candles
            WHERE provider = %s AND symbol = %s AND timeframe_minutes = %s
              AND candle_at >= %s AND candle_at <= %s
            ORDER BY candle_at ASC
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (PROVIDER, str(symbol).upper(), int(timeframe_minutes), start, end))
                rows = cursor.fetchall()
        return [
            [
                candle_at.isoformat(),
                float(open_price), float(high), float(low), float(close),
                float(volume or 0),
                float(open_interest) if open_interest is not None else None,
            ]
            for candle_at, open_price, high, low, close, volume, open_interest in rows
        ]

    async def read_symbol(self, symbol, timeframe_minutes, start, end):
        return await asyncio.to_thread(
            self._read_symbol_sync, symbol, timeframe_minutes, start, end,
        )


    def _read_symbol_contract_segments_sync(self, symbol, timeframe_minutes, start, end):
        sql = """
            SELECT trading_symbol, expiry_date, candle_at, open, high, low, close, volume, open_interest
            FROM commodity_candles
            WHERE provider = %s AND symbol = %s AND timeframe_minutes = %s
              AND candle_at >= %s AND candle_at <= %s
            ORDER BY candle_at ASC, trading_symbol ASC
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (PROVIDER, str(symbol).upper(), int(timeframe_minutes), start, end))
                rows = cursor.fetchall()
        segments = []
        current = None
        for trading_symbol, expiry_date, candle_at, open_price, high, low, close, volume, open_interest in rows:
            if current is None or current["trading_symbol"] != trading_symbol:
                current = {
                    "trading_symbol": trading_symbol,
                    "expiry_date": expiry_date.isoformat() if expiry_date else None,
                    "candles": [],
                }
                segments.append(current)
            current["candles"].append([
                candle_at.isoformat(),
                float(open_price), float(high), float(low), float(close),
                float(volume or 0),
                float(open_interest) if open_interest is not None else None,
            ])
        return segments

    async def read_symbol_contract_segments(self, symbol, timeframe_minutes, start, end):
        return await asyncio.to_thread(
            self._read_symbol_contract_segments_sync,
            symbol, timeframe_minutes, start, end,
        )

    def _status_sync(self):
        sql = """
            SELECT symbol, timeframe_minutes, COUNT(*), MIN(candle_at), MAX(candle_at)
            FROM commodity_candles
            GROUP BY symbol, timeframe_minutes
            ORDER BY symbol, timeframe_minutes
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()
        return {
            "enabled": True,
            "series": [
                {
                    "symbol": symbol,
                    "timeframe_minutes": timeframe,
                    "candles": count,
                    "first_at": first.isoformat() if first else None,
                    "last_at": last.isoformat() if last else None,
                }
                for symbol, timeframe, count, first, last in rows
            ],
        }

    async def status(self):
        return await asyncio.to_thread(self._status_sync)


def _number(value, default=0):
    try:
        return Decimal(str(default if value is None else value))
    except Exception:
        return Decimal(str(default))


def _records(symbol, contract, timeframe_minutes, rows, collected_at):
    deduplicated = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            candle_at = _ts(row[0])
            open_price, high, low, close = (_number(value) for value in row[1:5])
        except (TypeError, ValueError, OverflowError):
            continue
        if min(open_price, high, low, close) <= 0 or high < low:
            continue
        record = {
            "provider": PROVIDER,
            "symbol": symbol,
            "exchange": contract.get("exchange") or "MCX",
            "segment": contract.get("segment") or "COMMODITY",
            "trading_symbol": contract.get("trading_symbol"),
            "groww_symbol": contract.get("groww_symbol"),
            "expiry_date": contract.get("expiry_date"),
            "timeframe_minutes": timeframe_minutes,
            "candle_at": candle_at,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": max(Decimal(0), _number(row[5] if len(row) > 5 else 0)),
            "open_interest": _number(row[6]) if len(row) > 6 and row[6] is not None else None,
            "collected_at": collected_at,
        }
        deduplicated[candle_at.isoformat()] = record
    return [deduplicated[key] for key in sorted(deduplicated)]


async def collect_completed_commodity_candles(
    provider,
    store: CandleStore,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
):
    collected_at = _ts(now or datetime.now(IST))
    backfill_start = collected_at - timedelta(days=max(1, int(lookback_days)))
    await store.initialize()
    series = []
    total = 0
    for symbol in SYMBOLS:
        contract = await resolve_nearest_mcx_future(symbol)
        for timeframe, interval in TIMEFRAMES.items():
            latest = await store.latest_candle_at(contract["trading_symbol"], interval)
            overlap_start = _ts(latest) - timedelta(minutes=interval * 2) if latest else backfill_start
            fetch_start = max(backfill_start, overlap_start)
            fetched = await _fetch_chunked(
                provider, contract, interval, fetch_start, collected_at,
            )
            completed = completed_rows(fetched, collected_at, interval)
            records = _records(symbol, contract, interval, completed, collected_at)
            upserted = await store.upsert(records)
            total += upserted
            series.append({
                "symbol": symbol,
                "contract": contract.get("trading_symbol"),
                "timeframe": timeframe,
                "fetch_start": fetch_start.isoformat(),
                "fetched": len(fetched),
                "completed": len(completed),
                "upserted": upserted,
                "latest_completed_at": records[-1]["candle_at"].isoformat() if records else None,
            })
    return {
        "status": "COLLECTED",
        "provider": PROVIDER,
        "collected_at": collected_at.isoformat(),
        "lookback_days": max(1, int(lookback_days)),
        "upserted": total,
        "series": series,
        "idempotency_key": "provider+trading_symbol+timeframe_minutes+candle_at",
    }


async def backfill_commodity_candles(
    provider,
    store: CandleStore,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe_minutes: int = 5,
):
    """Persist one bounded historical commodity range. Intended for orchestrated backfill."""
    symbol = str(symbol).upper().strip()
    if symbol not in SYMBOLS:
        raise ValueError(f"symbol must be one of {', '.join(SYMBOLS)}")
    interval = int(timeframe_minutes)
    if interval not in {5, 15, 60}:
        raise ValueError("timeframe_minutes must be 5, 15 or 60")
    start_at, end_at = _ts(start), _ts(end)
    if end_at <= start_at:
        raise ValueError("end must be after start")
    if end_at - start_at > timedelta(days=2, minutes=5):
        raise ValueError("backfill range must not exceed 2 days")
    await store.initialize()
    contract = await resolve_nearest_mcx_future(symbol)
    fetched = await _fetch_chunked(provider, contract, interval, start_at, end_at)
    completed = completed_rows(fetched, end_at + timedelta(minutes=interval), interval)
    collected_at = datetime.now(IST)
    records = _records(symbol, contract, interval, completed, collected_at)
    upserted = await store.upsert(records)
    return {
        "status": "BACKFILLED",
        "research_only": True,
        "symbol": symbol,
        "contract": contract.get("trading_symbol"),
        "timeframe_minutes": interval,
        "start": start_at.isoformat(),
        "end": end_at.isoformat(),
        "fetched": len(fetched),
        "completed": len(completed),
        "upserted": upserted,
    }


async def resolve_historical_mcx_contract(symbol: str, when: datetime):
    """Resolve the deterministic front-month contract for a historical timestamp."""
    from .commodity_continuous_backtest import discover_mcx_contracts

    symbol = str(symbol).upper().strip()
    target = _ts(when).date()
    contracts = await discover_mcx_contracts(symbol)
    eligible = []
    for contract in contracts:
        try:
            expiry = datetime.fromisoformat(str(contract.get("expiry_date"))[:10]).date()
        except Exception:
            continue
        if expiry >= target:
            eligible.append((expiry, contract))
    if not eligible:
        raise RuntimeError(f"No discovered {symbol} contract covers {target.isoformat()}")
    return dict(min(eligible, key=lambda item: item[0])[1])


async def backfill_continuous_commodity_candles(
    provider,
    store: CandleStore,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe_minutes: int = 5,
):
    """Persist one bounded range using the historical front-month contract for that date."""
    symbol = str(symbol).upper().strip()
    interval = int(timeframe_minutes)
    start_at, end_at = _ts(start), _ts(end)
    if end_at <= start_at:
        raise ValueError("end must be after start")
    if end_at - start_at > timedelta(days=1, minutes=5):
        raise ValueError("continuous backfill range must not exceed 1 day")
    contract = await resolve_historical_mcx_contract(symbol, start_at)
    await store.initialize()
    fetched = await _fetch_chunked(provider, contract, interval, start_at, end_at)
    completed = completed_rows(fetched, end_at + timedelta(minutes=interval), interval)
    collected_at = datetime.now(IST)
    records = _records(symbol, contract, interval, completed, collected_at)
    upserted = await store.upsert(records)
    return {
        "status": "BACKFILLED_CONTINUOUS",
        "research_only": True,
        "rollover_method": "EXPIRY_BOUNDARY_FRONT_MONTH",
        "symbol": symbol,
        "contract": contract.get("trading_symbol"),
        "contract_expiry": contract.get("expiry_date"),
        "timeframe_minutes": interval,
        "start": start_at.isoformat(),
        "end": end_at.isoformat(),
        "fetched": len(fetched),
        "completed": len(completed),
        "upserted": upserted,
    }
