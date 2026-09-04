from __future__ import annotations

import asyncio

from .commodity_candle_collector import SCHEMA_SQL as GENERIC_CANDLE_SCHEMA_SQL


TABLE_NAME = "copper_candle_observations"
PROVENANCE_ID = "COPPER_5M_FIRST_SEEN_IMMUTABLE_CANDLE_OBSERVATIONS_V1"
TIMEFRAME_MINUTES = 5

COPPER_CANDLE_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL CHECK (symbol = 'COPPER'),
    exchange TEXT NOT NULL,
    segment TEXT NOT NULL,
    trading_symbol TEXT NOT NULL,
    groww_symbol TEXT,
    expiry_date DATE,
    timeframe_minutes SMALLINT NOT NULL CHECK (timeframe_minutes = {TIMEFRAME_MINUTES}),
    candle_at TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC NOT NULL DEFAULT 0,
    open_interest NUMERIC,
    collected_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider, trading_symbol, timeframe_minutes, candle_at)
);
CREATE INDEX IF NOT EXISTS copper_candle_observations_time_idx
    ON {TABLE_NAME} (candle_at DESC, collected_at);
"""

TRIGGER_SQL = f"""
CREATE OR REPLACE FUNCTION capture_copper_5m_candle_first_seen()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.symbol = 'COPPER' AND NEW.timeframe_minutes = {TIMEFRAME_MINUTES} THEN
        INSERT INTO {TABLE_NAME} (
            provider, symbol, exchange, segment, trading_symbol, groww_symbol,
            expiry_date, timeframe_minutes, candle_at, open, high, low, close,
            volume, open_interest, collected_at
        ) VALUES (
            NEW.provider, NEW.symbol, NEW.exchange, NEW.segment,
            NEW.trading_symbol, NEW.groww_symbol, NEW.expiry_date,
            NEW.timeframe_minutes, NEW.candle_at, NEW.open, NEW.high, NEW.low,
            NEW.close, NEW.volume, NEW.open_interest, NEW.collected_at
        )
        ON CONFLICT (provider, trading_symbol, timeframe_minutes, candle_at)
        DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS copper_5m_candle_first_seen_trigger ON commodity_candles;
CREATE TRIGGER copper_5m_candle_first_seen_trigger
AFTER INSERT ON commodity_candles
FOR EACH ROW
EXECUTE FUNCTION capture_copper_5m_candle_first_seen();
"""


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def initialize_copper_candle_observation_store_sync(database_url: str) -> None:
    """Install prospective first-seen COPPER 5m capture with no legacy backfill.

    The generic ``commodity_candles`` table remains untouched and may continue to
    revise same-key rows through its historical UPSERT. This dedicated table is
    populated only on the first INSERT of a COPPER 5-minute candle. A conflicting
    generic UPDATE does not fire this AFTER INSERT trigger, so the captured OHLCV
    and OI state is immutable.

    Existing generic Copper candles are intentionally not copied. Only future
    first inserts after installation can claim ``PROVENANCE_ID``.
    """
    database_url = str(database_url or "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required for COPPER candle provenance")
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(GENERIC_CANDLE_SCHEMA_SQL)
            cursor.execute(COPPER_CANDLE_SCHEMA_SQL)
            cursor.execute(TRIGGER_SQL)


async def initialize_copper_candle_observation_store(database_url: str) -> None:
    await asyncio.to_thread(initialize_copper_candle_observation_store_sync, database_url)


def register_copper_candle_observation_startup(app, settings) -> None:
    @app.on_event("startup")
    async def _initialize_copper_candle_provenance() -> None:
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            return
        await initialize_copper_candle_observation_store(database_url)
