"""Optional Postgres persistence for immutable resolved BTC experience memory.

This store is separate from market-data PIT persistence because resolved learning
legitimately contains outcomes. Availability is governed by ``resolved_at`` and a
case is readable only when ``resolved_at < current_decision_at``. No update/delete
mutation path is exposed and persistence never starts collection or execution.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.crypto_btc_experience_store import ResolvedBtcExperienceRecord, same_resolved_experience

TABLE_NAME = "crypto_btc_resolved_experience_v1"
PROVENANCE_ID = "BTC_RESOLVED_EXPERIENCE_POSTGRES_V1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    natural_key TEXT PRIMARY KEY,
    click_id TEXT NOT NULL UNIQUE,
    decision_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL,
    instrument_type TEXT NOT NULL CHECK (instrument_type = 'OPTIONS'),
    outcome_type TEXT NOT NULL CHECK (outcome_type IN ('TRADE_CLOSED', 'NO_TRADE_LEARNING')),
    source_version TEXT NOT NULL,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    record_fingerprint TEXT NOT NULL UNIQUE,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (resolved_at > decision_at)
);
CREATE INDEX IF NOT EXISTS crypto_btc_experience_resolved_idx
    ON {TABLE_NAME} (resolved_at ASC);
CREATE INDEX IF NOT EXISTS crypto_btc_experience_decision_idx
    ON {TABLE_NAME} (decision_at ASC);
"""

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    natural_key, click_id, decision_at, resolved_at, instrument_type,
    outcome_type, source_version, payload, payload_hash, record_fingerprint,
    provenance_id
) VALUES (
    %(natural_key)s, %(click_id)s, %(decision_at)s, %(resolved_at)s,
    %(instrument_type)s, %(outcome_type)s, %(source_version)s,
    %(payload)s::jsonb, %(payload_hash)s, %(record_fingerprint)s,
    %(provenance_id)s
)
ON CONFLICT (natural_key) DO NOTHING
RETURNING record_fingerprint;
"""

SELECT_BY_KEY_SQL = f"""
SELECT natural_key, click_id, decision_at, resolved_at, instrument_type,
       outcome_type, source_version, payload, payload_hash, record_fingerprint,
       provenance_id
FROM {TABLE_NAME}
WHERE natural_key = %s;
"""

VISIBLE_STRICTLY_BEFORE_SQL = f"""
SELECT natural_key, click_id, decision_at, resolved_at, instrument_type,
       outcome_type, source_version, payload, payload_hash, record_fingerprint,
       provenance_id
FROM {TABLE_NAME}
WHERE resolved_at < %s
ORDER BY resolved_at ASC, click_id ASC;
"""

MANIFEST_SQL = f"""
SELECT outcome_type, COUNT(*)::BIGINT
FROM {TABLE_NAME}
GROUP BY outcome_type
ORDER BY outcome_type ASC;
"""

_COLUMNS = (
    "natural_key", "click_id", "decision_at", "resolved_at", "instrument_type",
    "outcome_type", "source_version", "payload", "payload_hash",
    "record_fingerprint", "provenance_id",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
    raise ValueError("stored resolved-experience payload is not a JSON object")


def _row_dict(row) -> dict:
    if row is None:
        raise ValueError("resolved-experience row is missing")
    values = dict(zip(_COLUMNS, row, strict=True))
    values["payload"] = _decode_payload(values["payload"])
    for key in ("decision_at", "resolved_at"):
        value = values.get(key)
        if isinstance(value, datetime):
            values[key] = _utc(value).isoformat()
    return values


def postgres_experience_params(record: ResolvedBtcExperienceRecord) -> dict:
    frozen = record.frozen_dict()
    return {
        "natural_key": frozen["natural_key"],
        "click_id": frozen["click_id"],
        "decision_at": datetime.fromisoformat(frozen["decision_at"]),
        "resolved_at": datetime.fromisoformat(frozen["resolved_at"]),
        "instrument_type": frozen["instrument_type"],
        "outcome_type": frozen["outcome_type"],
        "source_version": frozen["source_version"],
        "payload": _canonical(frozen["payload"]),
        "payload_hash": frozen["payload_hash"],
        "record_fingerprint": frozen["record_fingerprint"],
        "provenance_id": PROVENANCE_ID,
    }


def _candidate_identity_from_params(params: dict) -> dict:
    return {
        "click_id": params["click_id"],
        "decision_at": _utc(params["decision_at"]).isoformat(),
        "resolved_at": _utc(params["resolved_at"]).isoformat(),
        "instrument_type": params["instrument_type"],
        "outcome_type": params["outcome_type"],
        "source_version": params["source_version"],
        "payload_hash": params["payload_hash"],
    }


class PostgresBtcExperienceStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required for Postgres BTC experience persistence")

    async def initialize(self) -> dict:
        await asyncio.to_thread(self._initialize_sync)
        return {
            "status": "BTC_EXPERIENCE_POSTGRES_SCHEMA_READY",
            "table": TABLE_NAME,
            "collection_started": False,
            "execution_started": False,
        }

    def _initialize_sync(self) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    async def insert_resolved(self, record: ResolvedBtcExperienceRecord) -> dict:
        return await asyncio.to_thread(self._insert_resolved_sync, record)

    def _insert_resolved_sync(self, record: ResolvedBtcExperienceRecord) -> dict:
        params = postgres_experience_params(record)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_SQL, params)
                inserted = cur.fetchone()
                if inserted is not None:
                    conn.commit()
                    return {
                        "status": "INSERTED_RESOLVED_EXPERIENCE",
                        "natural_key": params["natural_key"],
                        "record_fingerprint": params["record_fingerprint"],
                    }
                cur.execute(SELECT_BY_KEY_SQL, (params["natural_key"],))
                existing = _row_dict(cur.fetchone())
            conn.commit()

        candidate = _candidate_identity_from_params(params)
        if same_resolved_experience(existing, candidate):
            return {
                "status": "IDEMPOTENT_RESOLVED_EXPERIENCE",
                "natural_key": params["natural_key"],
                "record_fingerprint": existing["record_fingerprint"],
                "resolved_at": existing["resolved_at"],
            }
        raise ValueError("conflicting later experience cannot overwrite immutable Postgres resolved memory")

    async def visible_strictly_before(self, decision_at: datetime) -> list[dict]:
        return await asyncio.to_thread(self._visible_strictly_before_sync, decision_at)

    def _visible_strictly_before_sync(self, decision_at: datetime) -> list[dict]:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(VISIBLE_STRICTLY_BEFORE_SQL, (_utc(decision_at),))
                return [_row_dict(row) for row in cur.fetchall()]

    async def manifest(self) -> dict:
        return await asyncio.to_thread(self._manifest_sync)

    def _manifest_sync(self) -> dict:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(MANIFEST_SQL)
                rows = cur.fetchall()
        by_outcome = {str(outcome): int(count) for outcome, count in rows}
        return {
            "version": "BTC_EXPERIENCE_POSTGRES_MANIFEST_V1",
            "record_count": sum(by_outcome.values()),
            "by_outcome_type": by_outcome,
            "insert_only": True,
            "visible_only_strictly_after_resolution": True,
            "market_data_pit_archive_used_for_outcomes": False,
            "execution_started": False,
        }


def architecture_contract() -> dict:
    return {
        "version": "BTC_EXPERIENCE_POSTGRES_CONTRACT_V1",
        "backend": "POSTGRES",
        "backend_automatically_selected": False,
        "database_url_required": True,
        "schema_initialization_starts_collection": False,
        "schema_initialization_starts_execution": False,
        "insert_only": True,
        "update_existing_record_allowed": False,
        "delete_existing_record_via_this_module_allowed": False,
        "first_resolved_record_wins": True,
        "exact_duplicate_idempotent": True,
        "conflicting_duplicate_rejected": True,
        "visibility_operator": "resolved_at < current_decision_at",
        "same_timestamp_resolution_visible": False,
        "market_data_pit_archive_used_for_outcomes": False,
        "futures_state_allowed": False,
        "options_execution_enabled": False,
        "futures_execution_enabled": False,
        "research_only": True,
    }
