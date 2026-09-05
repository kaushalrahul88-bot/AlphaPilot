"""Insert-only Postgres persistence for prospective macro feed-availability audits.

Operational availability evidence is intentionally stored outside the BTC market-
data PIT archive. A failed or delayed retrieval is evidence about feed timeliness,
not a neutral market observation. No update/delete path or trade side effect is
exposed.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from app.crypto_macro_live_availability_audit import MacroLiveAvailabilityAttempt

TABLE_NAME = "crypto_macro_live_availability_attempt_v1"
PROVENANCE_ID = "MASSIVE_MACRO_LIVE_AVAILABILITY_POSTGRES_V1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    natural_key TEXT PRIMARY KEY,
    event_key TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('CPI', 'EMPLOYMENT_SITUATION')),
    release_at TIMESTAMPTZ NOT NULL,
    reaction_window_end TIMESTAMPTZ NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'AVAILABLE_WITHIN_LATENCY',
        'AVAILABLE_TOO_LATE',
        'UNAVAILABLE_OR_PROVIDER_ERROR'
    )),
    availability_latency_seconds DOUBLE PRECISION NOT NULL CHECK (availability_latency_seconds >= 0),
    provider TEXT NOT NULL CHECK (provider = 'MASSIVE_CME_FUTURES'),
    attempt_fingerprint TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (reaction_window_end > release_at),
    CHECK (attempted_at >= reaction_window_end),
    CHECK (completed_at >= attempted_at)
);
CREATE INDEX IF NOT EXISTS crypto_macro_live_availability_event_idx
    ON {TABLE_NAME} (event_key, attempted_at ASC);
CREATE INDEX IF NOT EXISTS crypto_macro_live_availability_status_idx
    ON {TABLE_NAME} (status, completed_at ASC);
"""

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    natural_key, event_key, event_type, release_at, reaction_window_end,
    attempted_at, completed_at, status, availability_latency_seconds,
    provider, attempt_fingerprint, payload, provenance_id
) VALUES (
    %(natural_key)s, %(event_key)s, %(event_type)s, %(release_at)s,
    %(reaction_window_end)s, %(attempted_at)s, %(completed_at)s, %(status)s,
    %(availability_latency_seconds)s, %(provider)s, %(attempt_fingerprint)s,
    %(payload)s::jsonb, %(provenance_id)s
)
ON CONFLICT (natural_key) DO NOTHING
RETURNING attempt_fingerprint;
"""

SELECT_BY_KEY_SQL = f"""
SELECT natural_key, event_key, event_type, release_at, reaction_window_end,
       attempted_at, completed_at, status, availability_latency_seconds,
       provider, attempt_fingerprint, payload, provenance_id
FROM {TABLE_NAME}
WHERE natural_key = %s;
"""

HAS_SUCCESS_SQL = f"""
SELECT 1
FROM {TABLE_NAME}
WHERE event_key = %s
  AND status = 'AVAILABLE_WITHIN_LATENCY'
LIMIT 1;
"""

LIST_ATTEMPTS_SQL = f"""
SELECT natural_key, event_key, event_type, release_at, reaction_window_end,
       attempted_at, completed_at, status, availability_latency_seconds,
       provider, attempt_fingerprint, payload, provenance_id
FROM {TABLE_NAME}
ORDER BY release_at ASC, attempted_at ASC, natural_key ASC;
"""

_COLUMNS = (
    "natural_key", "event_key", "event_type", "release_at", "reaction_window_end",
    "attempted_at", "completed_at", "status", "availability_latency_seconds",
    "provider", "attempt_fingerprint", "payload", "provenance_id",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("availability audit timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def attempt_payload(attempt: MacroLiveAvailabilityAttempt) -> dict:
    attempt.validated()
    return {
        "event_key": attempt.event_key,
        "event_type": attempt.event_type,
        "release_at": _utc(attempt.release_at).isoformat(),
        "reaction_window_end": _utc(attempt.reaction_window_end).isoformat(),
        "attempted_at": _utc(attempt.attempted_at).isoformat(),
        "completed_at": _utc(attempt.completed_at).isoformat(),
        "status": attempt.status,
        "availability_latency_seconds": float(attempt.availability_latency_seconds),
        "provider": attempt.provider,
        "nasdaq_contract_ticker": attempt.nasdaq_contract_ticker,
        "euro_fx_contract_ticker": attempt.euro_fx_contract_ticker,
        "failure_kind": attempt.failure_kind,
        "historical_reconstruction_used_as_live_proof": False,
        "direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_confirmation_enabled": False,
    }


def attempt_natural_key(attempt: MacroLiveAvailabilityAttempt) -> str:
    attempt.validated()
    identity = {
        "event_key": attempt.event_key,
        "event_type": attempt.event_type,
        "release_at": _utc(attempt.release_at).isoformat(),
        "attempted_at": _utc(attempt.attempted_at).isoformat(),
        "provider": attempt.provider,
    }
    return sha256(_canonical(identity).encode("utf-8")).hexdigest()


def postgres_availability_params(attempt: MacroLiveAvailabilityAttempt) -> dict:
    payload = attempt_payload(attempt)
    return {
        "natural_key": attempt_natural_key(attempt),
        "event_key": attempt.event_key,
        "event_type": attempt.event_type,
        "release_at": _utc(attempt.release_at),
        "reaction_window_end": _utc(attempt.reaction_window_end),
        "attempted_at": _utc(attempt.attempted_at),
        "completed_at": _utc(attempt.completed_at),
        "status": attempt.status,
        "availability_latency_seconds": float(attempt.availability_latency_seconds),
        "provider": attempt.provider,
        "attempt_fingerprint": attempt.fingerprint(),
        "payload": _canonical(payload),
        "provenance_id": PROVENANCE_ID,
    }


def _decode_payload(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise ValueError("stored availability-audit payload is not a JSON object")


def _row_dict(row) -> dict:
    if row is None:
        raise ValueError("availability-audit row is missing")
    values = dict(zip(_COLUMNS, row, strict=True))
    values["payload"] = _decode_payload(values["payload"])
    for key in ("release_at", "reaction_window_end", "attempted_at", "completed_at"):
        if isinstance(values.get(key), datetime):
            values[key] = _utc(values[key]).isoformat()
    return values


def payload_to_attempt(payload: dict) -> MacroLiveAvailabilityAttempt:
    required = {
        "event_key", "event_type", "release_at", "reaction_window_end",
        "attempted_at", "completed_at", "status", "availability_latency_seconds",
        "provider", "nasdaq_contract_ticker", "euro_fx_contract_ticker", "failure_kind",
    }
    if not required.issubset(payload):
        raise ValueError("stored availability-audit payload is incomplete")
    return MacroLiveAvailabilityAttempt(
        event_key=str(payload["event_key"]),
        event_type=str(payload["event_type"]),
        release_at=datetime.fromisoformat(str(payload["release_at"])),
        reaction_window_end=datetime.fromisoformat(str(payload["reaction_window_end"])),
        attempted_at=datetime.fromisoformat(str(payload["attempted_at"])),
        completed_at=datetime.fromisoformat(str(payload["completed_at"])),
        status=str(payload["status"]),
        availability_latency_seconds=float(payload["availability_latency_seconds"]),
        provider=str(payload["provider"]),
        nasdaq_contract_ticker=payload.get("nasdaq_contract_ticker"),
        euro_fx_contract_ticker=payload.get("euro_fx_contract_ticker"),
        failure_kind=payload.get("failure_kind"),
    ).validated()


class PostgresMacroLiveAvailabilityStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required for macro live-availability persistence")

    async def initialize(self) -> dict:
        await asyncio.to_thread(self._initialize_sync)
        return {
            "status": "MACRO_LIVE_AVAILABILITY_POSTGRES_SCHEMA_READY",
            "table": TABLE_NAME,
            "collection_started": False,
            "live_confirmation_enabled": False,
            "execution_started": False,
        }

    def _initialize_sync(self) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    async def insert_attempt(self, attempt: MacroLiveAvailabilityAttempt) -> dict:
        return await asyncio.to_thread(self._insert_attempt_sync, attempt)

    def _insert_attempt_sync(self, attempt: MacroLiveAvailabilityAttempt) -> dict:
        params = postgres_availability_params(attempt)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_SQL, params)
                inserted = cur.fetchone()
                if inserted is not None:
                    conn.commit()
                    return {
                        "status": "INSERTED_AVAILABILITY_ATTEMPT",
                        "natural_key": params["natural_key"],
                        "attempt_fingerprint": params["attempt_fingerprint"],
                    }
                cur.execute(SELECT_BY_KEY_SQL, (params["natural_key"],))
                existing = _row_dict(cur.fetchone())
            conn.commit()
        if existing["attempt_fingerprint"] == params["attempt_fingerprint"]:
            return {
                "status": "IDEMPOTENT_AVAILABILITY_ATTEMPT",
                "natural_key": params["natural_key"],
                "attempt_fingerprint": existing["attempt_fingerprint"],
            }
        raise ValueError("conflicting availability attempt cannot overwrite immutable Postgres audit history")

    async def has_success(self, event_key: str) -> bool:
        return await asyncio.to_thread(self._has_success_sync, event_key)

    def _has_success_sync(self, event_key: str) -> bool:
        if not str(event_key or "").strip():
            raise ValueError("event_key is required")
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(HAS_SUCCESS_SQL, (event_key,))
                return cur.fetchone() is not None

    async def list_attempts(self) -> list[MacroLiveAvailabilityAttempt]:
        return await asyncio.to_thread(self._list_attempts_sync)

    def _list_attempts_sync(self) -> list[MacroLiveAvailabilityAttempt]:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(LIST_ATTEMPTS_SQL)
                rows = [_row_dict(row) for row in cur.fetchall()]
        return [payload_to_attempt(row["payload"]) for row in rows]


def architecture_contract() -> dict:
    return {
        "version": "MASSIVE_MACRO_LIVE_AVAILABILITY_POSTGRES_V1",
        "backend": "POSTGRES",
        "database_url_required": True,
        "operational_audit_separate_from_market_data_pit": True,
        "insert_only": True,
        "update_existing_attempt_allowed": False,
        "delete_existing_attempt_via_this_module_allowed": False,
        "exact_duplicate_idempotent": True,
        "conflicting_duplicate_rejected": True,
        "failed_attempts_preserved": True,
        "schema_initialization_starts_collection": False,
        "schema_initialization_enables_live_confirmation": False,
        "schema_initialization_starts_execution": False,
        "direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
