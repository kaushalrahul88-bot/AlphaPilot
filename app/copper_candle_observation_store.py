from __future__ import annotations

import asyncio
from datetime import datetime

from .commodity_candle_collector import PROVIDER, SCHEMA_SQL as GENERIC_CANDLE_SCHEMA_SQL


TABLE_NAME = "copper_candle_observations"
PROVENANCE_ID = "COPPER_5M_FIRST_SEEN_IMMUTABLE_CANDLE_OBSERVATIONS_V1"
SYMBOL = "COPPER"
TIMEFRAME_MINUTES = 5

COPPER_CANDLE_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL CHECK (symbol = '{SYMBOL}'),
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
CREATE INDEX IF NOT EXISTS copper_candle_observations_contract_time_idx
    ON {TABLE_NAME} (trading_symbol, timeframe_minutes, candle_at DESC);
"""

TRIGGER_SQL = f"""
CREATE OR REPLACE FUNCTION capture_copper_5m_candle_first_seen()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.symbol = '{SYMBOL}' AND NEW.timeframe_minutes = {TIMEFRAME_MINUTES} THEN
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

INSERT_FIRST_SEEN_SQL = f"""
INSERT INTO {TABLE_NAME} (
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
DO NOTHING
RETURNING 1;
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
    observations captured after installation can claim ``PROVENANCE_ID``.
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


class CopperCandleObservationStore:
    """Direct first-seen store used by the bounded prospective Copper collector.

    Unlike the legacy generic commodity table this class never rewrites OHLCV/OI.
    The first state AlphaPilot actually observes is the only state retained for a
    contract/timeframe/candle key. A historical candle fetched for the first time
    today is therefore available only from its real ``collected_at`` timestamp,
    never from the candle's older market timestamp.
    """

    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for COPPER candle observations")

    async def initialize(self) -> None:
        await initialize_copper_candle_observation_store(self.database_url)

    def _latest_candle_at_sync(self, trading_symbol: str) -> datetime | None:
        sql = f"""
            SELECT MAX(candle_at)
            FROM {TABLE_NAME}
            WHERE provider = %s
              AND symbol = %s
              AND trading_symbol = %s
              AND timeframe_minutes = %s
        """
        with _connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (PROVIDER, SYMBOL, trading_symbol, TIMEFRAME_MINUTES))
                row = cursor.fetchone()
        return row[0] if row else None

    async def latest_candle_at(self, trading_symbol: str) -> datetime | None:
        return await asyncio.to_thread(self._latest_candle_at_sync, trading_symbol)

    @staticmethod
    def _validate_record(record: dict) -> None:
        if str(record.get("symbol") or "").upper() != SYMBOL:
            raise ValueError("CopperCandleObservationStore accepts COPPER records only")
        if int(record.get("timeframe_minutes") or 0) != TIMEFRAME_MINUTES:
            raise ValueError("CopperCandleObservationStore accepts 5-minute records only")
        if not str(record.get("trading_symbol") or "").upper().startswith("COPPER"):
            raise ValueError("Copper record trading_symbol must be an exact COPPER contract")

    def _insert_first_seen_sync(self, records: list[dict]) -> int:
        if not records:
            return 0
        for record in records:
            self._validate_record(record)
        inserted = 0
        with _connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                for record in records:
                    cursor.execute(INSERT_FIRST_SEEN_SQL, record)
                    inserted += 1 if cursor.fetchone() else 0
        return inserted

    async def insert_first_seen(self, records: list[dict]) -> int:
        return await asyncio.to_thread(self._insert_first_seen_sync, records)

    def _read_pit_sync(
        self,
        start: datetime,
        end: datetime,
        as_of: datetime,
        trading_symbol: str | None = None,
    ) -> list[list]:
        params: list = [PROVIDER, SYMBOL, TIMEFRAME_MINUTES, start, end, TIMEFRAME_MINUTES, as_of, as_of]
        contract_sql = ""
        if trading_symbol:
            contract_sql = " AND trading_symbol = %s"
            params.append(str(trading_symbol))
        sql = f"""
            SELECT candle_at, open, high, low, close, volume, open_interest
            FROM {TABLE_NAME}
            WHERE provider = %s
              AND symbol = %s
              AND timeframe_minutes = %s
              AND candle_at >= %s
              AND candle_at <= %s
              AND candle_at + (%s * INTERVAL '1 minute') <= %s
              AND collected_at <= %s
              {contract_sql}
            ORDER BY candle_at ASC
        """
        with _connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
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

    async def read_pit(
        self,
        start: datetime,
        end: datetime,
        as_of: datetime,
        trading_symbol: str | None = None,
    ) -> list[list]:
        return await asyncio.to_thread(
            self._read_pit_sync,
            start,
            end,
            as_of,
            trading_symbol,
        )

    def _status_sync(self) -> dict:
        sql = f"""
            SELECT COUNT(*), COUNT(DISTINCT trading_symbol), MIN(candle_at),
                   MAX(candle_at), MAX(collected_at)
            FROM {TABLE_NAME}
            WHERE provider = %s AND symbol = %s AND timeframe_minutes = %s
        """
        with _connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (PROVIDER, SYMBOL, TIMEFRAME_MINUTES))
                row = cursor.fetchone() or (0, 0, None, None, None)
        return {
            "status": "ACTIVE",
            "symbol": SYMBOL,
            "timeframe_minutes": TIMEFRAME_MINUTES,
            "rows": int(row[0] or 0),
            "contracts": int(row[1] or 0),
            "first_candle_at": row[2].isoformat() if row[2] else None,
            "last_candle_at": row[3].isoformat() if row[3] else None,
            "latest_collected_at": row[4].isoformat() if row[4] else None,
            "first_seen_immutable": True,
            "provenance_id": PROVENANCE_ID,
            "historical_backfill_used": False,
        }

    async def status(self) -> dict:
        return await asyncio.to_thread(self._status_sync)


def register_copper_candle_observation_startup(app, settings) -> None:
    @app.on_event("startup")
    async def _initialize_copper_candle_provenance() -> None:
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            return
        await initialize_copper_candle_observation_store(database_url)