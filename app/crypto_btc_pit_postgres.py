"""Optional Postgres persistence for the immutable BTC point-in-time archive.

This module implements the storage contract from ``crypto_btc_pit_archive``
without changing its semantics. Merely importing or initializing this module
never starts collection. First-seen records are insert-only: exact stored
fingerprints are idempotent and conflicting later observations are rejected.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.crypto_btc_pit_archive import BtcPitArchiveRecord

TABLE_NAME = "crypto_btc_pit_archive_v1"
PROVENANCE_ID = "BTC_PIT_POSTGRES_FIRST_SEEN_V1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    natural_key TEXT PRIMARY KEY,
    dataset TEXT NOT NULL,
    provider TEXT NOT NULL,
    source_key TEXT NOT NULL,
    event_at TIMESTAMPTZ NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    source_version TEXT NULL,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    record_fingerprint TEXT NOT NULL UNIQUE,
    point_in_time_proven BOOLEAN NOT NULL CHECK (point_in_time_proven = TRUE),
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dataset, provider, source_key)
);
CREATE INDEX IF NOT EXISTS crypto_btc_pit_dataset_seen_idx
    ON {TABLE_NAME} (dataset, first_seen_at ASC);
CREATE INDEX IF NOT EXISTS crypto_btc_pit_provider_seen_idx
    ON {TABLE_NAME} (provider, first_seen_at ASC);
"""

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    natural_key, dataset, provider, source_key, event_at, first_seen_at,
    source_version, payload, payload_hash, record_fingerprint,
    point_in_time_proven, provenance_id
) VALUES (
    %(natural_key)s, %(dataset)s, %(provider)s, %(source_key)s,
    %(event_at)s, %(first_seen_at)s, %(source_version)s,
    %(payload)s::jsonb, %(payload_hash)s, %(record_fingerprint)s,
    TRUE, %(provenance_id)s
)
ON CONFLICT (natural_key) DO NOTHING
RETURNING record_fingerprint;
"""

SELECT_BY_KEY_SQL = f"""
SELECT natural_key, dataset, provider, source_key, event_at, first_seen_at,
       source_version, payload, payload_hash, record_fingerprint,
       point_in_time_proven, provenance_id
FROM {TABLE_NAME}
WHERE natural_key = %s;
"""

VISIBLE_AS_OF_SQL = f"""
SELECT natural_key, dataset, provider, source_key, event_at, first_seen_at,
       source_version, payload, payload_hash, record_fingerprint,
       point_in_time_proven, provenance_id
FROM {TABLE_NAME}
WHERE first_seen_at <= %s
  AND (%s IS NULL OR dataset = %s)
ORDER BY first_seen_at ASC, dataset ASC, source_key ASC;
"""

MANIFEST_SQL = f"""
SELECT dataset, COUNT(*)::BIGINT
FROM {TABLE_NAME}
GROUP BY dataset
ORDER BY dataset ASC;
"""

_COLUMNS = (
    "natural_key", "dataset", "provider", "source_key", "event_at",
    "first_seen_at", "source_version", "payload", "payload_hash",
    "record_fingerprint", "point_in_time_proven", "provenance_id",
)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _decode_payload(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise ValueError("stored PIT payload is not a JSON object")


def _row_dict(row) -> dict:
    if row is None:
        raise ValueError("PIT archive row is missing")
    values = dict(zip(_COLUMNS, row, strict=True))
    values["payload"] = _decode_payload(values["payload"])
    for key in ("event_at", "first_seen_at"):
        value = values.get(key)
        if isinstance(value, datetime):
            values[key] = _utc(value).isoformat()
    values["point_in_time_proven"] = bool(values["point_in_time_proven"])
    return values


def postgres_record_params(record: BtcPitArchiveRecord) -> dict:
    frozen = record.frozen_dict()
    return {
        "natural_key": frozen["natural_key"],
        "dataset": frozen["dataset"],
        "provider": frozen["provider"],
        "source_key": frozen["source_key"],
        "event_at": None if frozen["event_at"] is None else datetime.fromisoformat(frozen["event_at"]),
        "first_seen_at": datetime.fromisoformat(frozen["first_seen_at"]),
        "source_version": frozen["source_version"],
        "payload": _canonical(frozen["payload"]),
        "payload_hash": frozen["payload_hash"],
        "record_fingerprint": frozen["record_fingerprint"],
        "provenance_id": PROVENANCE_ID,
    }


class PostgresBtcPitArchiveStore:
    """Insert-only Postgres implementation of the BTC PIT archive contract."""

    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required for Postgres BTC PIT persistence")

    async def initialize(self) -> dict:
        await asyncio.to_thread(self._initialize_sync)
        return {
            "status": "BTC_PIT_POSTGRES_SCHEMA_READY",
            "table": TABLE_NAME,
            "collection_started": False,
        }

    def _initialize_sync(self) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    async def insert_first_seen(self, record: BtcPitArchiveRecord) -> dict:
        return await asyncio.to_thread(self._insert_first_seen_sync, record)

    def _insert_first_seen_sync(self, record: BtcPitArchiveRecord) -> dict:
        params = postgres_record_params(record)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_SQL, params)
                inserted = cur.fetchone()
                if inserted is not None:
                    conn.commit()
                    return {
                        "status": "INSERTED_FIRST_SEEN",
                        "natural_key": params["natural_key"],
                        "record_fingerprint": params["record_fingerprint"],
                    }
                cur.execute(SELECT_BY_KEY_SQL, (params["natural_key"],))
                existing = _row_dict(cur.fetchone())
            conn.commit()

        if existing["record_fingerprint"] == params["record_fingerprint"]:
            return {
                "status": "IDEMPOTENT_DUPLICATE",
                "natural_key": params["natural_key"],
                "record_fingerprint": existing["record_fingerprint"],
            }
        raise ValueError("conflicting later observation cannot overwrite immutable Postgres first-seen record")

    async def visible_as_of(self, as_of: datetime, *, dataset: str | None = None) -> list[dict]:
        return await asyncio.to_thread(self._visible_as_of_sync, as_of, dataset)

    def _visible_as_of_sync(self, as_of: datetime, dataset: str | None) -> list[dict]:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(VISIBLE_AS_OF_SQL, (_utc(as_of), dataset, dataset))
                rows = [_row_dict(row) for row in cur.fetchall()]
        return rows

    async def manifest(self) -> dict:
        return await asyncio.to_thread(self._manifest_sync)

    def _manifest_sync(self) -> dict:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(MANIFEST_SQL)
                rows = cur.fetchall()
        by_dataset = {str(dataset): int(count) for dataset, count in rows}
        return {
            "version": "BTC_PIT_POSTGRES_MANIFEST_V1",
            "record_count": sum(by_dataset.values()),
            "by_dataset": by_dataset,
            "immutable_first_seen": True,
            "overwrite_allowed": False,
            "outcome_fields_allowed": False,
            "collection_started": False,
        }


def architecture_contract() -> dict:
    return {
        "version": "BTC_PIT_POSTGRES_CONTRACT_V1",
        "backend": "POSTGRES",
        "backend_is_automatically_selected": False,
        "database_url_required": True,
        "schema_initialization_starts_collection": False,
        "insert_only": True,
        "update_existing_record_allowed": False,
        "delete_existing_record_via_this_module_allowed": False,
        "first_seen_wins": True,
        "exact_duplicate_is_idempotent": True,
        "conflicting_duplicate_is_rejected": True,
        "visible_before_first_seen": False,
        "outcome_fields_allowed": False,
        "options_execution_enabled": False,
        "futures_execution_enabled": False,
        "research_only": True,
    }
