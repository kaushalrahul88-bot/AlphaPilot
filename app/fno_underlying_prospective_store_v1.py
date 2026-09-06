"""Immutable Postgres tape for prospective underlying-only edge discovery."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .fno_underlying_prospective_v1 import PROTOCOL_ID

EPISODE_TABLE = "fno_underlying_prospective_episodes_v1"
OUTCOME_TABLE = "fno_underlying_prospective_outcomes_v1"
PROVENANCE_ID = "FNO_UNDERLYING_PROSPECTIVE_POSTGRES_V1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {EPISODE_TABLE} (
    episode_id TEXT PRIMARY KEY,
    protocol_id TEXT NOT NULL,
    capture_slot_at TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    capture_latency_seconds NUMERIC NOT NULL,
    underlying_symbol TEXT NOT NULL,
    batch_index INTEGER NOT NULL,
    reference_price NUMERIC NOT NULL CHECK (reference_price > 0),
    research_action TEXT NOT NULL CHECK (research_action IN ('LONG','SHORT','NO_TRADE')),
    technical_status TEXT,
    multi_timeframe_score NUMERIC,
    live_execution_enabled BOOLEAN NOT NULL CHECK (live_execution_enabled = FALSE),
    broker_orders_created BOOLEAN NOT NULL CHECK (broker_orders_created = FALSE),
    options_used BOOLEAN NOT NULL CHECK (options_used = FALSE),
    futures_used BOOLEAN NOT NULL CHECK (futures_used = FALSE),
    capital_committed NUMERIC NOT NULL CHECK (capital_committed = 0),
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (protocol_id, underlying_symbol, capture_slot_at)
);
CREATE INDEX IF NOT EXISTS fno_underlying_prospective_slot_idx
    ON {EPISODE_TABLE} (capture_slot_at DESC, underlying_symbol);

CREATE TABLE IF NOT EXISTS {OUTCOME_TABLE} (
    episode_id TEXT NOT NULL REFERENCES {EPISODE_TABLE}(episode_id),
    horizon_code TEXT NOT NULL CHECK (horizon_code IN ('15m','30m','60m','90m','EOD')),
    outcome_due_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL,
    resolution_status TEXT NOT NULL,
    classification TEXT NOT NULL,
    end_price NUMERIC,
    raw_return_pct NUMERIC,
    directional_return_pct NUMERIC,
    max_up_pct NUMERIC,
    max_down_pct NUMERIC,
    mfe_pct NUMERIC,
    mae_pct NUMERIC,
    max_abs_excursion_pct NUMERIC,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (episode_id, horizon_code),
    CHECK (resolved_at >= outcome_due_at)
);
CREATE INDEX IF NOT EXISTS fno_underlying_prospective_outcome_due_idx
    ON {OUTCOME_TABLE} (outcome_due_at DESC);
"""

IMMUTABILITY_FUNCTION = "fno_underlying_prospective_reject_mutation_v1"
IMMUTABILITY_SQL = f"""
CREATE OR REPLACE FUNCTION {IMMUTABILITY_FUNCTION}()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'prospective underlying F&O research tape is immutable';
END;
$$ LANGUAGE plpgsql;
"""
for _table, _prefix in (
    (EPISODE_TABLE, "fno_underlying_prospective_episode"),
    (OUTCOME_TABLE, "fno_underlying_prospective_outcome"),
):
    IMMUTABILITY_SQL += f"""
DROP TRIGGER IF EXISTS {_prefix}_reject_row_mutation_v1 ON {_table};
CREATE TRIGGER {_prefix}_reject_row_mutation_v1
BEFORE UPDATE OR DELETE ON {_table}
FOR EACH ROW EXECUTE FUNCTION {IMMUTABILITY_FUNCTION}();
DROP TRIGGER IF EXISTS {_prefix}_reject_truncate_v1 ON {_table};
CREATE TRIGGER {_prefix}_reject_truncate_v1
BEFORE TRUNCATE ON {_table}
FOR EACH STATEMENT EXECUTE FUNCTION {IMMUTABILITY_FUNCTION}();
"""


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("underlying prospective timestamps must be timezone-aware")
    return stamp.astimezone(timezone.utc)


def _decode(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        result = json.loads(value)
        if isinstance(result, dict):
            return result
    raise ValueError("stored payload must be a JSON object")


def _episode_params(record: Mapping[str, Any]) -> dict[str, Any]:
    if record.get("protocol_id") != PROTOCOL_ID:
        raise ValueError("unexpected underlying prospective protocol")
    action = str((record.get("decision") or {}).get("action") or "NO_TRADE").upper()
    if action not in {"LONG", "SHORT", "NO_TRADE"}:
        raise ValueError("invalid underlying research action")
    if record.get("future_outcome_present_in_decision") is not False:
        raise ValueError("future outcome cannot be present in a frozen decision")
    if record.get("outcome_used_for_decision") is not False:
        raise ValueError("outcome cannot influence a frozen decision")
    forbidden_true = (
        "option_chain_used", "option_premium_used", "option_oi_iv_greeks_used",
        "futures_used", "broker_orders_created", "live_execution",
    )
    if any(record.get(key) is not False for key in forbidden_true):
        raise ValueError("prospective underlying tape forbids derivatives/execution inputs")
    if float(record.get("capital_committed") or 0) != 0:
        raise ValueError("prospective underlying tape forbids capital")
    slot = _utc(record["capture_slot_at"])
    captured = _utc(record["captured_at"])
    if captured < slot:
        raise ValueError("captured_at cannot precede capture slot")
    reference = float(record["reference_price"])
    if reference <= 0:
        raise ValueError("reference_price must be positive")
    technical = record.get("technical") or {}
    payload = _canonical(record)
    return {
        "episode_id": str(record["episode_id"]),
        "protocol_id": PROTOCOL_ID,
        "capture_slot_at": slot,
        "captured_at": captured,
        "capture_latency_seconds": float(record["capture_latency_seconds"]),
        "underlying_symbol": str(record["underlying_symbol"]).upper(),
        "batch_index": int(record["batch_index"]),
        "reference_price": reference,
        "research_action": action,
        "technical_status": technical.get("status"),
        "multi_timeframe_score": technical.get("multi_timeframe_score"),
        "live_execution_enabled": False,
        "broker_orders_created": False,
        "options_used": False,
        "futures_used": False,
        "capital_committed": 0,
        "payload": payload,
        "payload_hash": _hash(record),
        "provenance_id": PROVENANCE_ID,
    }


def _outcome_params(record: Mapping[str, Any]) -> dict[str, Any]:
    code = str(record["horizon_code"])
    if code not in {"15m", "30m", "60m", "90m", "EOD"}:
        raise ValueError("invalid outcome horizon")
    due = _utc(record["outcome_due_at"])
    resolved = _utc(record["resolved_at"])
    if resolved < due:
        raise ValueError("outcome cannot be resolved before due time")
    payload = _canonical(record)
    return {
        "episode_id": str(record["episode_id"]),
        "horizon_code": code,
        "outcome_due_at": due,
        "resolved_at": resolved,
        "resolution_status": str(record["resolution_status"]),
        "classification": str(record["classification"]),
        "end_price": record.get("end_price"),
        "raw_return_pct": record.get("raw_return_pct"),
        "directional_return_pct": record.get("directional_return_pct"),
        "max_up_pct": record.get("max_up_pct"),
        "max_down_pct": record.get("max_down_pct"),
        "mfe_pct": record.get("mfe_pct"),
        "mae_pct": record.get("mae_pct"),
        "max_abs_excursion_pct": record.get("max_abs_excursion_pct"),
        "payload": payload,
        "payload_hash": _hash(record),
        "provenance_id": PROVENANCE_ID,
    }


class FnoUnderlyingProspectiveStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required")

    def _initialize_sync(self) -> dict[str, Any]:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
                cur.execute(IMMUTABILITY_SQL)
            conn.commit()
        return {
            "status": "READY",
            "protocol_id": PROTOCOL_ID,
            "episode_table": EPISODE_TABLE,
            "outcome_table": OUTCOME_TABLE,
            "database_immutable": True,
        }

    async def initialize(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._initialize_sync)

    def _insert_episode_sync(self, record: Mapping[str, Any]) -> dict[str, Any]:
        params = _episode_params(record)
        sql = f"""
        INSERT INTO {EPISODE_TABLE} (
            episode_id, protocol_id, capture_slot_at, captured_at,
            capture_latency_seconds, underlying_symbol, batch_index,
            reference_price, research_action, technical_status,
            multi_timeframe_score, live_execution_enabled,
            broker_orders_created, options_used, futures_used,
            capital_committed, payload, payload_hash, provenance_id
        ) VALUES (
            %(episode_id)s, %(protocol_id)s, %(capture_slot_at)s, %(captured_at)s,
            %(capture_latency_seconds)s, %(underlying_symbol)s, %(batch_index)s,
            %(reference_price)s, %(research_action)s, %(technical_status)s,
            %(multi_timeframe_score)s, %(live_execution_enabled)s,
            %(broker_orders_created)s, %(options_used)s, %(futures_used)s,
            %(capital_committed)s, %(payload)s::jsonb, %(payload_hash)s,
            %(provenance_id)s
        )
        ON CONFLICT (protocol_id, underlying_symbol, capture_slot_at) DO NOTHING
        RETURNING episode_id;
        """
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
        return {
            "status": "INSERTED" if row else "ALREADY_EXISTS",
            "episode_id": params["episode_id"],
        }

    async def insert_episode(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._insert_episode_sync, dict(record))

    def _pending_sync(self, since: datetime, limit: int) -> list[dict[str, Any]]:
        sql = f"""
        SELECT e.episode_id, e.capture_slot_at, e.captured_at,
               e.underlying_symbol, e.reference_price, e.research_action,
               e.payload,
               COALESCE(array_agg(o.horizon_code) FILTER (WHERE o.horizon_code IS NOT NULL), ARRAY[]::text[])
        FROM {EPISODE_TABLE} e
        LEFT JOIN {OUTCOME_TABLE} o ON o.episode_id=e.episode_id
        WHERE e.protocol_id=%s AND e.capture_slot_at >= %s
        GROUP BY e.episode_id, e.capture_slot_at, e.captured_at,
                 e.underlying_symbol, e.reference_price, e.research_action, e.payload
        ORDER BY e.capture_slot_at ASC, e.underlying_symbol ASC
        LIMIT %s;
        """
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (PROTOCOL_ID, _utc(since), int(limit)))
                rows = cur.fetchall()
        result = []
        for row in rows:
            result.append({
                "episode_id": row[0],
                "capture_slot_at": row[1].astimezone(timezone.utc),
                "captured_at": row[2].astimezone(timezone.utc),
                "underlying_symbol": row[3],
                "reference_price": float(row[4]),
                "research_action": row[5],
                "payload": _decode(row[6]),
                "resolved_horizons": set(row[7] or []),
            })
        return result

    async def pending_episodes(self, since: datetime, limit: int = 500) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._pending_sync, since, limit)

    def _insert_outcome_sync(self, record: Mapping[str, Any]) -> dict[str, Any]:
        params = _outcome_params(record)
        sql = f"""
        INSERT INTO {OUTCOME_TABLE} (
            episode_id, horizon_code, outcome_due_at, resolved_at,
            resolution_status, classification, end_price, raw_return_pct,
            directional_return_pct, max_up_pct, max_down_pct, mfe_pct,
            mae_pct, max_abs_excursion_pct, payload, payload_hash, provenance_id
        ) VALUES (
            %(episode_id)s, %(horizon_code)s, %(outcome_due_at)s, %(resolved_at)s,
            %(resolution_status)s, %(classification)s, %(end_price)s,
            %(raw_return_pct)s, %(directional_return_pct)s, %(max_up_pct)s,
            %(max_down_pct)s, %(mfe_pct)s, %(mae_pct)s,
            %(max_abs_excursion_pct)s, %(payload)s::jsonb, %(payload_hash)s,
            %(provenance_id)s
        )
        ON CONFLICT (episode_id, horizon_code) DO NOTHING
        RETURNING episode_id;
        """
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
        return {
            "status": "INSERTED" if row else "ALREADY_EXISTS",
            "episode_id": params["episode_id"],
            "horizon_code": params["horizon_code"],
        }

    async def insert_outcome(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._insert_outcome_sync, dict(record))

    def _status_sync(self) -> dict[str, Any]:
        sql = f"""
        SELECT
          (SELECT COUNT(*) FROM {EPISODE_TABLE} WHERE protocol_id=%s),
          (SELECT COUNT(*) FROM {OUTCOME_TABLE} o JOIN {EPISODE_TABLE} e USING(episode_id) WHERE e.protocol_id=%s),
          (SELECT MIN(capture_slot_at) FROM {EPISODE_TABLE} WHERE protocol_id=%s),
          (SELECT MAX(capture_slot_at) FROM {EPISODE_TABLE} WHERE protocol_id=%s);
        """
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (PROTOCOL_ID, PROTOCOL_ID, PROTOCOL_ID, PROTOCOL_ID))
                row = cur.fetchone()
        return {
            "protocol_id": PROTOCOL_ID,
            "episodes": int(row[0] or 0),
            "outcomes": int(row[1] or 0),
            "first_capture_slot_at": row[2].astimezone(timezone.utc).isoformat() if row[2] else None,
            "last_capture_slot_at": row[3].astimezone(timezone.utc).isoformat() if row[3] else None,
            "database_immutable": True,
            "live_execution": False,
            "capital_committed": 0,
        }

    async def status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._status_sync)
