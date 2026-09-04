from __future__ import annotations

import asyncio

from .commodity_option_snapshot_collector import SCHEMA_SQL as GENERIC_OPTION_SCHEMA_SQL


TABLE_NAME = "copper_option_observations"
PROVENANCE_ID = "COPPER_FIRST_SEEN_IMMUTABLE_OPTION_OBSERVATIONS_V1"

COPPER_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    provider TEXT NOT NULL,
    underlying_symbol TEXT NOT NULL CHECK (underlying_symbol = 'COPPER'),
    exchange TEXT NOT NULL,
    segment TEXT NOT NULL,
    trading_symbol TEXT NOT NULL,
    groww_symbol TEXT NOT NULL,
    expiry_date DATE NOT NULL,
    strike NUMERIC NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('CE', 'PE')),
    lot_size INTEGER,
    sample_bucket_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    underlying_price NUMERIC,
    last_price NUMERIC NOT NULL,
    volume NUMERIC,
    open_interest NUMERIC,
    bid_price NUMERIC,
    ask_price NUMERIC,
    raw_payload TEXT,
    collected_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider, trading_symbol, sample_bucket_at)
);
CREATE INDEX IF NOT EXISTS copper_option_observations_time_idx
    ON {TABLE_NAME} (sample_bucket_at DESC);
CREATE INDEX IF NOT EXISTS copper_option_observations_contract_idx
    ON {TABLE_NAME} (expiry_date, option_type, strike, sample_bucket_at DESC);
"""

TRIGGER_SQL = f"""
CREATE OR REPLACE FUNCTION capture_copper_option_first_seen()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.underlying_symbol = 'COPPER' THEN
        INSERT INTO {TABLE_NAME} (
            provider, underlying_symbol, exchange, segment, trading_symbol, groww_symbol,
            expiry_date, strike, option_type, lot_size, sample_bucket_at, observed_at,
            underlying_price, last_price, volume, open_interest, bid_price, ask_price,
            raw_payload, collected_at
        ) VALUES (
            NEW.provider, NEW.underlying_symbol, NEW.exchange, NEW.segment,
            NEW.trading_symbol, NEW.groww_symbol, NEW.expiry_date, NEW.strike,
            NEW.option_type, NEW.lot_size, NEW.sample_bucket_at, NEW.observed_at,
            NEW.underlying_price, NEW.last_price, NEW.volume, NEW.open_interest,
            NEW.bid_price, NEW.ask_price, NEW.raw_payload, NEW.collected_at
        )
        ON CONFLICT (provider, trading_symbol, sample_bucket_at) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS copper_option_first_seen_trigger
    ON commodity_option_snapshots;
CREATE TRIGGER copper_option_first_seen_trigger
AFTER INSERT ON commodity_option_snapshots
FOR EACH ROW
EXECUTE FUNCTION capture_copper_option_first_seen();
"""


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def initialize_copper_option_observation_store_sync(database_url: str) -> None:
    """Install prospective first-seen Copper capture without legacy backfill.

    The generic option table keeps its existing mutable same-bucket UPSERT
    semantics. This Copper-only table is populated solely by an AFTER INSERT
    trigger. PostgreSQL's conflicting ON CONFLICT DO UPDATE path does not fire
    this AFTER INSERT trigger for the conflicting row, so a later same-bucket
    generic update cannot replace the first captured Copper observation.

    Existing rows in ``commodity_option_snapshots`` are intentionally never
    copied here. Therefore only observations first inserted after this store is
    installed can claim the provenance id exported by this module.
    """
    database_url = str(database_url or "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required for COPPER option provenance")
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(GENERIC_OPTION_SCHEMA_SQL)
            cursor.execute(COPPER_SCHEMA_SQL)
            cursor.execute(TRIGGER_SQL)


async def initialize_copper_option_observation_store(database_url: str) -> None:
    await asyncio.to_thread(initialize_copper_option_observation_store_sync, database_url)


def register_copper_option_observation_startup(app, settings) -> None:
    """Install prospective immutable Copper capture before production traffic."""

    @app.on_event("startup")
    async def _initialize_copper_option_provenance() -> None:
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            return
        await initialize_copper_option_observation_store(database_url)
