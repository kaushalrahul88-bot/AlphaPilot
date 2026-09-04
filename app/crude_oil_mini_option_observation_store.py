from __future__ import annotations

import asyncio

from .commodity_option_snapshot_collector import SCHEMA_SQL as GENERIC_OPTION_SCHEMA_SQL


TABLE_NAME = "crude_oil_mini_option_observations"
PROVENANCE_ID = "CRUDEOILM_FIRST_SEEN_IMMUTABLE_OPTION_OBSERVATIONS_V1"

MINI_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    provider TEXT NOT NULL,
    underlying_symbol TEXT NOT NULL CHECK (underlying_symbol = 'CRUDEOILM'),
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
CREATE INDEX IF NOT EXISTS crude_oil_mini_option_observations_time_idx
    ON {TABLE_NAME} (sample_bucket_at DESC);
CREATE INDEX IF NOT EXISTS crude_oil_mini_option_observations_contract_idx
    ON {TABLE_NAME} (expiry_date, option_type, strike, sample_bucket_at DESC);
"""

TRIGGER_SQL = f"""
CREATE OR REPLACE FUNCTION capture_crude_oil_mini_option_first_seen()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.underlying_symbol = 'CRUDEOILM' THEN
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

DROP TRIGGER IF EXISTS crude_oil_mini_option_first_seen_trigger
    ON commodity_option_snapshots;
CREATE TRIGGER crude_oil_mini_option_first_seen_trigger
AFTER INSERT ON commodity_option_snapshots
FOR EACH ROW
EXECUTE FUNCTION capture_crude_oil_mini_option_first_seen();
"""


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def initialize_crude_oil_mini_option_observation_store_sync(database_url: str) -> None:
    """Install prospective first-seen capture without backfilling legacy snapshots.

    The generic option table retains its legacy mutable same-bucket UPSERT semantics.
    This Mini-only table is populated by an AFTER INSERT trigger. PostgreSQL's
    ON CONFLICT DO UPDATE path does not fire this AFTER INSERT trigger for the
    conflicting row, so subsequent same-bucket updates cannot overwrite or create
    a later-state Mini observation. Existing generic rows are intentionally never
    copied into the immutable table.
    """
    database_url = str(database_url or "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required for CRUDEOILM option provenance")
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            # Reuse the canonical generic schema definition only to guarantee the
            # trigger target exists on a fresh database. Its UPSERT behavior is not
            # changed by this module.
            cursor.execute(GENERIC_OPTION_SCHEMA_SQL)
            cursor.execute(MINI_SCHEMA_SQL)
            cursor.execute(TRIGGER_SQL)


async def initialize_crude_oil_mini_option_observation_store(database_url: str) -> None:
    await asyncio.to_thread(
        initialize_crude_oil_mini_option_observation_store_sync,
        database_url,
    )


def register_crude_oil_mini_option_observation_startup(app, settings) -> None:
    """Guarantee immutable Mini capture is installed before Render serves traffic."""

    @app.on_event("startup")
    async def _initialize_crude_oil_mini_option_provenance() -> None:
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            return
        await initialize_crude_oil_mini_option_observation_store(database_url)
