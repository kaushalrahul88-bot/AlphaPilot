"""Insert-only Postgres persistence for the prospective BTC thesis proof tape.

The proof tape is intentionally separate from both:
- the market-data PIT archive, which must never contain future outcomes; and
- the Options resolved-experience store, which requires actual option economics.

Frozen BTC-underlying decisions and their later BTC-only resolutions therefore
use separate insert-only tables. No UPDATE/DELETE path is exposed. Initializing
this schema never starts collection, a scheduler, execution, or live trading.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from app.crypto_btc_prospective_thesis_tape import (
    verify_frozen_prospective_btc_thesis,
    verify_prospective_btc_thesis_resolution,
)

DECISION_TABLE = "crypto_btc_prospective_thesis_decisions_v1"
RESOLUTION_TABLE = "crypto_btc_prospective_thesis_resolutions_v1"
PROVENANCE_ID = "BTC_PROSPECTIVE_THESIS_POSTGRES_V1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {DECISION_TABLE} (
    click_id TEXT PRIMARY KEY,
    decision_at TIMESTAMPTZ NOT NULL,
    outcome_due_at TIMESTAMPTZ NOT NULL,
    market_direction TEXT NOT NULL CHECK (market_direction IN ('BULLISH', 'BEARISH', 'UNKNOWN')),
    decision_fingerprint TEXT NOT NULL UNIQUE,
    tape_fingerprint TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (outcome_due_at > decision_at)
);
CREATE INDEX IF NOT EXISTS crypto_btc_prospective_thesis_due_idx
    ON {DECISION_TABLE} (outcome_due_at ASC);

CREATE TABLE IF NOT EXISTS {RESOLUTION_TABLE} (
    click_id TEXT PRIMARY KEY REFERENCES {DECISION_TABLE}(click_id),
    decision_at TIMESTAMPTZ NOT NULL,
    outcome_due_at TIMESTAMPTZ NOT NULL,
    resolution_at TIMESTAMPTZ NOT NULL,
    classification TEXT NOT NULL,
    decision_fingerprint TEXT NOT NULL,
    tape_fingerprint TEXT NOT NULL,
    resolution_fingerprint TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (resolution_at >= outcome_due_at),
    CHECK (outcome_due_at > decision_at)
);
CREATE INDEX IF NOT EXISTS crypto_btc_prospective_thesis_resolution_idx
    ON {RESOLUTION_TABLE} (resolution_at ASC);
"""

INSERT_DECISION_SQL = f"""
INSERT INTO {DECISION_TABLE} (
    click_id, decision_at, outcome_due_at, market_direction,
    decision_fingerprint, tape_fingerprint, payload, payload_hash, provenance_id
) VALUES (
    %(click_id)s, %(decision_at)s, %(outcome_due_at)s, %(market_direction)s,
    %(decision_fingerprint)s, %(tape_fingerprint)s, %(payload)s::jsonb,
    %(payload_hash)s, %(provenance_id)s
)
ON CONFLICT (click_id) DO NOTHING
RETURNING tape_fingerprint;
"""

SELECT_DECISION_SQL = f"""
SELECT click_id, decision_at, outcome_due_at, market_direction,
       decision_fingerprint, tape_fingerprint, payload, payload_hash, provenance_id
FROM {DECISION_TABLE}
WHERE click_id = %s;
"""

INSERT_RESOLUTION_SQL = f"""
INSERT INTO {RESOLUTION_TABLE} (
    click_id, decision_at, outcome_due_at, resolution_at, classification,
    decision_fingerprint, tape_fingerprint, resolution_fingerprint,
    payload, payload_hash, provenance_id
) VALUES (
    %(click_id)s, %(decision_at)s, %(outcome_due_at)s, %(resolution_at)s,
    %(classification)s, %(decision_fingerprint)s, %(tape_fingerprint)s,
    %(resolution_fingerprint)s, %(payload)s::jsonb, %(payload_hash)s,
    %(provenance_id)s
)
ON CONFLICT (click_id) DO NOTHING
RETURNING resolution_fingerprint;
"""

SELECT_RESOLUTION_SQL = f"""
SELECT click_id, decision_at, outcome_due_at, resolution_at, classification,
       decision_fingerprint, tape_fingerprint, resolution_fingerprint,
       payload, payload_hash, provenance_id
FROM {RESOLUTION_TABLE}
WHERE click_id = %s;
"""

PENDING_AS_OF_SQL = f"""
SELECT d.click_id, d.decision_at, d.outcome_due_at, d.market_direction,
       d.decision_fingerprint, d.tape_fingerprint, d.payload, d.payload_hash,
       d.provenance_id
FROM {DECISION_TABLE} d
LEFT JOIN {RESOLUTION_TABLE} r ON r.click_id = d.click_id
WHERE r.click_id IS NULL
  AND d.outcome_due_at <= %s
ORDER BY d.outcome_due_at ASC, d.click_id ASC;
"""

MANIFEST_SQL = f"""
SELECT
    (SELECT COUNT(*)::BIGINT FROM {DECISION_TABLE}) AS decisions,
    (SELECT COUNT(*)::BIGINT FROM {RESOLUTION_TABLE}) AS resolutions;
"""

_DECISION_COLUMNS = (
    "click_id", "decision_at", "outcome_due_at", "market_direction",
    "decision_fingerprint", "tape_fingerprint", "payload", "payload_hash",
    "provenance_id",
)
_RESOLUTION_COLUMNS = (
    "click_id", "decision_at", "outcome_due_at", "resolution_at",
    "classification", "decision_fingerprint", "tape_fingerprint",
    "resolution_fingerprint", "payload", "payload_hash", "provenance_id",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _payload_hash(value: dict) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


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
    raise ValueError("stored BTC thesis payload is not a JSON object")


def _row_dict(row, columns: tuple[str, ...]) -> dict:
    if row is None:
        raise ValueError("prospective BTC thesis row is missing")
    values = dict(zip(columns, row, strict=True))
    values["payload"] = _decode_payload(values["payload"])
    for key in ("decision_at", "outcome_due_at", "resolution_at"):
        if key in values and isinstance(values.get(key), datetime):
            values[key] = _utc(values[key]).isoformat()
    return values


def postgres_thesis_decision_params(record: dict) -> dict:
    if not verify_frozen_prospective_btc_thesis(record):
        raise ValueError("invalid frozen prospective BTC thesis")
    if record.get("future_outcome_present_in_decision") is not False:
        raise ValueError("frozen thesis persistence forbids future outcome in decision")
    if record.get("options_contract_data_used") is not False or record.get("options_execution_metadata_used") is not False:
        raise ValueError("frozen underlying thesis may not contain Options execution data")
    if record.get("options_trade_generated") is not False or record.get("futures_trade_generated") is not False:
        raise ValueError("frozen thesis persistence rejects generated trades")
    if record.get("live_execution") is not False or float(record.get("capital_committed", 0)) != 0:
        raise ValueError("frozen thesis persistence rejects live execution/capital")

    decision = record["decision"]
    payload = _canonical(record)
    return {
        "click_id": str(decision["click_id"]),
        "decision_at": _stamp(decision["decision_at"]),
        "outcome_due_at": _stamp(record["outcome_due_at"]),
        "market_direction": str(decision.get("market_direction") or "UNKNOWN").upper(),
        "decision_fingerprint": str(decision["decision_fingerprint"]),
        "tape_fingerprint": str(record["tape_fingerprint"]),
        "payload": payload,
        "payload_hash": _payload_hash(record),
        "provenance_id": PROVENANCE_ID,
    }


def postgres_thesis_resolution_params(resolution: dict) -> dict:
    if not verify_prospective_btc_thesis_resolution(resolution):
        raise ValueError("invalid resolved prospective BTC thesis outcome")
    if resolution.get("decision_rewritten") is not False or resolution.get("outcome_used_for_decision") is not False:
        raise ValueError("resolved thesis must preserve the frozen decision")
    if resolution.get("options_pnl_measured") is not False:
        raise ValueError("BTC thesis resolution may not contain Options P&L")
    if resolution.get("options_trade_generated") is not False or resolution.get("futures_trade_generated") is not False:
        raise ValueError("BTC thesis resolution rejects generated trades")
    if resolution.get("live_execution") is not False or float(resolution.get("capital_committed", 0)) != 0:
        raise ValueError("BTC thesis resolution rejects live execution/capital")
    outcome = resolution.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("status") != "OUTCOME_RESOLVED":
        raise ValueError("Postgres thesis resolution requires resolved BTC outcome")
    classification = str(outcome.get("classification") or "").strip()
    if not classification:
        raise ValueError("resolved BTC thesis outcome requires classification")

    return {
        "click_id": str(resolution["click_id"]),
        "decision_at": _stamp(resolution["decision_at"]),
        "outcome_due_at": _stamp(resolution["outcome_due_at"]),
        "resolution_at": _stamp(resolution["resolution_at"]),
        "classification": classification,
        "decision_fingerprint": str(resolution["decision_fingerprint"]),
        "tape_fingerprint": str(resolution["tape_fingerprint"]),
        "resolution_fingerprint": str(resolution["resolution_fingerprint"]),
        "payload": _canonical(resolution),
        "payload_hash": _payload_hash(resolution),
        "provenance_id": PROVENANCE_ID,
    }


def _same_decision(existing: dict, params: dict) -> bool:
    return (
        existing.get("click_id") == params.get("click_id")
        and existing.get("decision_fingerprint") == params.get("decision_fingerprint")
        and existing.get("tape_fingerprint") == params.get("tape_fingerprint")
        and existing.get("payload_hash") == params.get("payload_hash")
    )


def _same_resolution(existing: dict, params: dict) -> bool:
    return (
        existing.get("click_id") == params.get("click_id")
        and existing.get("decision_fingerprint") == params.get("decision_fingerprint")
        and existing.get("tape_fingerprint") == params.get("tape_fingerprint")
        and existing.get("resolution_fingerprint") == params.get("resolution_fingerprint")
        and existing.get("payload_hash") == params.get("payload_hash")
    )


class PostgresProspectiveBtcThesisTapeStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required for prospective BTC thesis persistence")

    async def initialize(self) -> dict:
        await asyncio.to_thread(self._initialize_sync)
        return {
            "status": "BTC_PROSPECTIVE_THESIS_POSTGRES_SCHEMA_READY",
            "decision_table": DECISION_TABLE,
            "resolution_table": RESOLUTION_TABLE,
            "collection_started": False,
            "execution_started": False,
        }

    def _initialize_sync(self) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    async def insert_frozen(self, record: dict) -> dict:
        return await asyncio.to_thread(self._insert_frozen_sync, record)

    def _insert_frozen_sync(self, record: dict) -> dict:
        params = postgres_thesis_decision_params(record)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_DECISION_SQL, params)
                inserted = cur.fetchone()
                if inserted is not None:
                    conn.commit()
                    return {
                        "status": "INSERTED_FROZEN_THESIS",
                        "click_id": params["click_id"],
                        "tape_fingerprint": params["tape_fingerprint"],
                    }
                cur.execute(SELECT_DECISION_SQL, (params["click_id"],))
                existing = _row_dict(cur.fetchone(), _DECISION_COLUMNS)
            conn.commit()
        if _same_decision(existing, params):
            return {
                "status": "IDEMPOTENT_FROZEN_THESIS",
                "click_id": params["click_id"],
                "tape_fingerprint": existing["tape_fingerprint"],
            }
        raise ValueError("conflicting prospective thesis cannot overwrite immutable Postgres decision")

    async def attach_resolution(self, resolution: dict) -> dict:
        return await asyncio.to_thread(self._attach_resolution_sync, resolution)

    def _attach_resolution_sync(self, resolution: dict) -> dict:
        params = postgres_thesis_resolution_params(resolution)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_DECISION_SQL, (params["click_id"],))
                decision_row = cur.fetchone()
                if decision_row is None:
                    raise ValueError("cannot persist BTC thesis resolution before frozen decision")
                decision = _row_dict(decision_row, _DECISION_COLUMNS)
                if decision["decision_fingerprint"] != params["decision_fingerprint"] or decision["tape_fingerprint"] != params["tape_fingerprint"]:
                    raise ValueError("BTC thesis resolution does not match frozen Postgres decision")

                cur.execute(INSERT_RESOLUTION_SQL, params)
                inserted = cur.fetchone()
                if inserted is not None:
                    conn.commit()
                    return {
                        "status": "ATTACHED_THESIS_RESOLUTION",
                        "click_id": params["click_id"],
                        "resolution_fingerprint": params["resolution_fingerprint"],
                    }
                cur.execute(SELECT_RESOLUTION_SQL, (params["click_id"],))
                existing = _row_dict(cur.fetchone(), _RESOLUTION_COLUMNS)
            conn.commit()
        if _same_resolution(existing, params):
            return {
                "status": "IDEMPOTENT_THESIS_RESOLUTION",
                "click_id": params["click_id"],
                "resolution_fingerprint": existing["resolution_fingerprint"],
            }
        raise ValueError("conflicting later BTC thesis outcome cannot overwrite immutable Postgres resolution")

    async def pending_as_of(self, as_of: datetime) -> list[dict]:
        return await asyncio.to_thread(self._pending_as_of_sync, as_of)

    def _pending_as_of_sync(self, as_of: datetime) -> list[dict]:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(PENDING_AS_OF_SQL, (_utc(as_of),))
                rows = [_row_dict(row, _DECISION_COLUMNS) for row in cur.fetchall()]
        return [row["payload"] for row in rows]

    async def manifest(self) -> dict:
        return await asyncio.to_thread(self._manifest_sync)

    def _manifest_sync(self) -> dict:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(MANIFEST_SQL)
                row = cur.fetchone()
        decisions = 0 if row is None else int(row[0])
        resolutions = 0 if row is None else int(row[1])
        return {
            "version": "BTC_PROSPECTIVE_THESIS_POSTGRES_MANIFEST_V1",
            "decision_count": decisions,
            "resolved_count": resolutions,
            "pending_count": max(0, decisions - resolutions),
            "decision_and_resolution_tables_separate": True,
            "insert_only": True,
            "options_pnl_measured": False,
            "execution_started": False,
        }


def architecture_contract() -> dict:
    return {
        "version": "BTC_PROSPECTIVE_THESIS_POSTGRES_CONTRACT_V1",
        "backend": "POSTGRES",
        "backend_automatically_selected": False,
        "database_url_required": True,
        "schema_initialization_starts_collection": False,
        "schema_initialization_starts_execution": False,
        "decision_and_resolution_tables_separate": True,
        "insert_only": True,
        "update_existing_decision_allowed": False,
        "update_existing_resolution_allowed": False,
        "delete_path_exposed": False,
        "unresolved_resolution_persisted": False,
        "market_data_pit_archive_used_for_outcomes": False,
        "options_experience_store_used_for_underlying_only_outcome": False,
        "options_pnl_measured": False,
        "futures_execution_enabled": False,
        "live_execution": False,
        "research_only": True,
    }
