"""Insert-only Postgres storage for Delta India BTC Options live-feed validation.

This table is intentionally separate from AlphaPilot's admitted BTC PIT archive.
Delta remains a candidate venue until live values are cross-checked against the
Delta India UI and the user explicitly adopts it for Crypto Options economics.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from app.delta_india_btc_options_public_provider import DeltaIndiaBtcOptionsSnapshot

TABLE_NAME = "crypto_btc_delta_options_probe_v1"
PROVENANCE_ID = "DELTA_INDIA_OPTIONS_PUBLIC_REST_FIRST_SEEN_V1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    snapshot_id TEXT PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL,
    venue TEXT NOT NULL CHECK (venue = 'DELTA_EXCHANGE_INDIA'),
    underlying TEXT NOT NULL CHECK (underlying = 'BTC'),
    nearest_expiry DATE NOT NULL,
    reference_spot_price DOUBLE PRECISION NOT NULL CHECK (reference_spot_price > 0),
    quote_count INTEGER NOT NULL CHECK (quote_count > 0),
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    point_in_time_proven BOOLEAN NOT NULL DEFAULT TRUE CHECK (point_in_time_proven = TRUE),
    candidate_only BOOLEAN NOT NULL DEFAULT TRUE CHECK (candidate_only = TRUE),
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS crypto_btc_delta_options_probe_seen_idx
    ON {TABLE_NAME} (first_seen_at ASC);
CREATE INDEX IF NOT EXISTS crypto_btc_delta_options_probe_expiry_idx
    ON {TABLE_NAME} (nearest_expiry, first_seen_at ASC);

CREATE OR REPLACE FUNCTION reject_crypto_btc_delta_options_probe_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'crypto_btc_delta_options_probe_v1 is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS crypto_btc_delta_options_probe_no_update_delete ON {TABLE_NAME};
CREATE TRIGGER crypto_btc_delta_options_probe_no_update_delete
BEFORE UPDATE OR DELETE ON {TABLE_NAME}
FOR EACH ROW EXECUTE FUNCTION reject_crypto_btc_delta_options_probe_mutation();

DROP TRIGGER IF EXISTS crypto_btc_delta_options_probe_no_truncate ON {TABLE_NAME};
CREATE TRIGGER crypto_btc_delta_options_probe_no_truncate
BEFORE TRUNCATE ON {TABLE_NAME}
FOR EACH STATEMENT EXECUTE FUNCTION reject_crypto_btc_delta_options_probe_mutation();
"""

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    snapshot_id, first_seen_at, venue, underlying, nearest_expiry,
    reference_spot_price, quote_count, payload, payload_hash,
    point_in_time_proven, candidate_only, provenance_id
) VALUES (
    %(snapshot_id)s, %(first_seen_at)s, 'DELTA_EXCHANGE_INDIA', 'BTC',
    %(nearest_expiry)s, %(reference_spot_price)s, %(quote_count)s,
    %(payload)s::jsonb, %(payload_hash)s, TRUE, TRUE, %(provenance_id)s
)
ON CONFLICT (snapshot_id) DO NOTHING
RETURNING snapshot_id;
"""

LATEST_SQL = f"""
SELECT snapshot_id, first_seen_at, nearest_expiry, reference_spot_price,
       quote_count, payload, payload_hash
FROM {TABLE_NAME}
ORDER BY first_seen_at DESC
LIMIT %s;
"""

MANIFEST_SQL = f"""
SELECT COUNT(*)::BIGINT, MIN(first_seen_at), MAX(first_seen_at)
FROM {TABLE_NAME};
"""


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def snapshot_params(snapshot: DeltaIndiaBtcOptionsSnapshot) -> dict[str, Any]:
    payload = snapshot.frozen_dict()
    payload_json = _canonical(payload)
    payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
    identity = f"{payload['first_seen_at']}|{payload_hash}".encode("utf-8")
    return {
        "snapshot_id": sha256(identity).hexdigest(),
        "first_seen_at": datetime.fromisoformat(payload["first_seen_at"]),
        "nearest_expiry": snapshot.nearest_expiry,
        "reference_spot_price": snapshot.reference_spot_price,
        "quote_count": len(snapshot.quotes),
        "payload": payload_json,
        "payload_hash": payload_hash,
        "provenance_id": PROVENANCE_ID,
    }


class PostgresDeltaIndiaOptionsProbeStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required for Delta India Options probe persistence")

    async def initialize(self) -> dict[str, Any]:
        await asyncio.to_thread(self._initialize_sync)
        return {
            "status": "DELTA_OPTIONS_PROBE_SCHEMA_READY",
            "table": TABLE_NAME,
            "collection_started": False,
        }

    def _initialize_sync(self) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    async def insert_first_seen(self, snapshot: DeltaIndiaBtcOptionsSnapshot) -> dict[str, Any]:
        return await asyncio.to_thread(self._insert_sync, snapshot)

    def _insert_sync(self, snapshot: DeltaIndiaBtcOptionsSnapshot) -> dict[str, Any]:
        params = snapshot_params(snapshot)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_SQL, params)
                inserted = cur.fetchone()
            conn.commit()
        return {
            "status": "INSERTED_FIRST_SEEN" if inserted else "IDEMPOTENT_DUPLICATE",
            "snapshot_id": params["snapshot_id"],
            "first_seen_at": _utc(params["first_seen_at"]).isoformat(),
            "quote_count": params["quote_count"],
        }

    async def manifest(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._manifest_sync)

    def _manifest_sync(self) -> dict[str, Any]:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(MANIFEST_SQL)
                count, first_seen, latest_seen = cur.fetchone()
        return {
            "version": "DELTA_OPTIONS_PROBE_POSTGRES_MANIFEST_V1",
            "record_count": int(count),
            "first_seen_at": None if first_seen is None else _utc(first_seen).isoformat(),
            "latest_first_seen_at": None if latest_seen is None else _utc(latest_seen).isoformat(),
            "candidate_only": True,
            "immutable": True,
            "execution_enabled": False,
        }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "DELTA_OPTIONS_PROBE_POSTGRES_CONTRACT_V1",
        "separate_from_admitted_btc_pit_archive": True,
        "insert_only": True,
        "database_update_allowed": False,
        "database_delete_allowed": False,
        "database_truncate_allowed": False,
        "candidate_only": True,
        "options_execution_enabled": False,
        "futures_execution_enabled": False,
        "research_only": True,
    }
