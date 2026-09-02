from __future__ import annotations

import asyncio
import json
from datetime import datetime

from .crude_oil_mini_direction_forward import VALIDATION_PHASE

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crude_oil_mini_direction_v2_captures (
    validation_phase TEXT NOT NULL,
    click_timestamp TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    trading_symbol TEXT NOT NULL,
    capture_fingerprint TEXT NOT NULL UNIQUE,
    source_state_sha256 TEXT NOT NULL,
    capture_json JSONB NOT NULL,
    source_state_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (validation_phase, click_timestamp)
);

CREATE TABLE IF NOT EXISTS crude_oil_mini_direction_v2_outcomes (
    capture_fingerprint TEXT PRIMARY KEY,
    click_timestamp TIMESTAMPTZ NOT NULL,
    matured_at TIMESTAMPTZ NOT NULL,
    outcome_fingerprint TEXT NOT NULL UNIQUE,
    outcome_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS crude_oil_mini_direction_v2_capture_misses (
    validation_phase TEXT NOT NULL,
    click_timestamp TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL,
    detail_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (validation_phase, click_timestamp)
);

CREATE INDEX IF NOT EXISTS crude_oil_mini_direction_v2_capture_time_idx
    ON crude_oil_mini_direction_v2_captures (click_timestamp ASC);
CREATE INDEX IF NOT EXISTS crude_oil_mini_direction_v2_outcome_time_idx
    ON crude_oil_mini_direction_v2_outcomes (click_timestamp ASC);
"""


class PostgresCrudeDirectionCaptureStore:
    """Append-only durable store for prospective Direction V2 evidence.

    Captures, source state, outcomes and misses are never updated in place. A repeated
    scheduler call is idempotent; a conflicting fingerprint is a hard integrity error.
    """

    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for Crude Direction V2 capture storage")

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url, connect_timeout=10)

    def _initialize_sync(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA_SQL)

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    @staticmethod
    def _json(value: dict) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _read_capture_sync(self, click_timestamp, validation_phase=VALIDATION_PHASE):
        sql = """
            SELECT captured_at, trading_symbol, capture_fingerprint, source_state_sha256,
                   capture_json, source_state_json
            FROM crude_oil_mini_direction_v2_captures
            WHERE validation_phase=%s AND click_timestamp=%s
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (validation_phase, click_timestamp))
                row = cursor.fetchone()
        if not row:
            return None
        capture_json, source_state_json = row[4], row[5]
        if isinstance(capture_json, str):
            capture_json = json.loads(capture_json)
        if isinstance(source_state_json, str):
            source_state_json = json.loads(source_state_json)
        return {
            "captured_at": row[0].isoformat(),
            "trading_symbol": row[1],
            "capture_fingerprint": row[2],
            "source_state_sha256": row[3],
            "capture": capture_json,
            "source_state": source_state_json,
        }

    async def read_capture(self, click_timestamp, validation_phase=VALIDATION_PHASE):
        return await asyncio.to_thread(self._read_capture_sync, click_timestamp, validation_phase)

    def _insert_capture_once_sync(
        self,
        *,
        click_timestamp,
        captured_at,
        trading_symbol,
        capture,
        source_state,
        source_state_sha256,
        validation_phase,
    ):
        sql = """
            INSERT INTO crude_oil_mini_direction_v2_captures
            (validation_phase, click_timestamp, captured_at, trading_symbol,
             capture_fingerprint, source_state_sha256, capture_json, source_state_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
            ON CONFLICT (validation_phase, click_timestamp) DO NOTHING
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    (
                        validation_phase,
                        click_timestamp,
                        captured_at,
                        str(trading_symbol),
                        str(capture["capture_fingerprint"]),
                        str(source_state_sha256),
                        self._json(capture),
                        self._json(source_state),
                    ),
                )
                inserted = cursor.rowcount == 1
        existing = self._read_capture_sync(click_timestamp, validation_phase)
        if not existing:
            raise RuntimeError("Direction V2 capture insert did not persist a row")
        if existing["capture_fingerprint"] != capture["capture_fingerprint"]:
            raise RuntimeError("Immutable Direction V2 capture conflict at the same scheduled click")
        if existing["source_state_sha256"] != source_state_sha256:
            raise RuntimeError("Immutable Direction V2 source-state conflict at the same scheduled click")
        return inserted

    async def insert_capture_once(self, **kwargs):
        return await asyncio.to_thread(self._insert_capture_once_sync, **kwargs)

    def _insert_outcome_once_sync(self, *, capture, outcome, matured_at):
        sql = """
            INSERT INTO crude_oil_mini_direction_v2_outcomes
            (capture_fingerprint, click_timestamp, matured_at, outcome_fingerprint, outcome_json)
            VALUES (%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (capture_fingerprint) DO NOTHING
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    (
                        capture["capture_fingerprint"],
                        capture["click_timestamp"],
                        matured_at,
                        outcome["outcome_fingerprint"],
                        self._json(outcome),
                    ),
                )
                inserted = cursor.rowcount == 1
        return inserted

    async def insert_outcome_once(self, **kwargs):
        return await asyncio.to_thread(self._insert_outcome_once_sync, **kwargs)

    def _record_miss_once_sync(self, *, click_timestamp, observed_at, reason, detail, validation_phase):
        sql = """
            INSERT INTO crude_oil_mini_direction_v2_capture_misses
            (validation_phase, click_timestamp, observed_at, reason, detail_json)
            VALUES (%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (validation_phase, click_timestamp) DO NOTHING
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    (
                        validation_phase,
                        click_timestamp,
                        observed_at,
                        str(reason),
                        self._json(detail),
                    ),
                )
                return cursor.rowcount == 1

    async def record_miss_once(self, **kwargs):
        return await asyncio.to_thread(self._record_miss_once_sync, **kwargs)

    def _list_captures_sync(self, validation_phase=VALIDATION_PHASE):
        sql = """
            SELECT click_timestamp, captured_at, trading_symbol, capture_fingerprint,
                   source_state_sha256, capture_json, source_state_json
            FROM crude_oil_mini_direction_v2_captures
            WHERE validation_phase=%s
            ORDER BY click_timestamp ASC
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (validation_phase,))
                rows = cursor.fetchall()
        out = []
        for click, captured, symbol, fingerprint, state_sha, capture_json, state_json in rows:
            if isinstance(capture_json, str):
                capture_json = json.loads(capture_json)
            if isinstance(state_json, str):
                state_json = json.loads(state_json)
            out.append({
                "click_timestamp": click.isoformat(),
                "captured_at": captured.isoformat(),
                "trading_symbol": symbol,
                "capture_fingerprint": fingerprint,
                "source_state_sha256": state_sha,
                "capture": capture_json,
                "source_state": state_json,
            })
        return out

    async def list_captures(self, validation_phase=VALIDATION_PHASE):
        return await asyncio.to_thread(self._list_captures_sync, validation_phase)

    def _list_outcomes_sync(self):
        sql = """
            SELECT capture_fingerprint, click_timestamp, matured_at, outcome_fingerprint, outcome_json
            FROM crude_oil_mini_direction_v2_outcomes
            ORDER BY click_timestamp ASC
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()
        out = []
        for capture_fp, click, matured, outcome_fp, payload in rows:
            if isinstance(payload, str):
                payload = json.loads(payload)
            out.append({
                "capture_fingerprint": capture_fp,
                "click_timestamp": click.isoformat(),
                "matured_at": matured.isoformat(),
                "outcome_fingerprint": outcome_fp,
                "outcome": payload,
            })
        return out

    async def list_outcomes(self):
        return await asyncio.to_thread(self._list_outcomes_sync)

    def _list_misses_sync(self, validation_phase=VALIDATION_PHASE):
        sql = """
            SELECT click_timestamp, observed_at, reason, detail_json
            FROM crude_oil_mini_direction_v2_capture_misses
            WHERE validation_phase=%s
            ORDER BY click_timestamp ASC
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (validation_phase,))
                rows = cursor.fetchall()
        out = []
        for click, observed, reason, detail in rows:
            if isinstance(detail, str):
                detail = json.loads(detail)
            out.append({
                "click_timestamp": click.isoformat(),
                "observed_at": observed.isoformat(),
                "reason": reason,
                "detail": detail,
            })
        return out

    async def list_misses(self, validation_phase=VALIDATION_PHASE):
        return await asyncio.to_thread(self._list_misses_sync, validation_phase)

    async def status(self) -> dict:
        captures, outcomes, misses = await asyncio.gather(
            self.list_captures(),
            self.list_outcomes(),
            self.list_misses(),
        )
        return {
            "mode": "CRUDE_OIL_MINI_DIRECTION_V2_CAPTURE_STORE_V1",
            "research_only": True,
            "append_only": True,
            "validation_phase": VALIDATION_PHASE,
            "captures": len(captures),
            "outcomes": len(outcomes),
            "misses": len(misses),
            "last_capture": captures[-1]["click_timestamp"] if captures else None,
            "last_outcome": outcomes[-1]["click_timestamp"] if outcomes else None,
            "last_miss": misses[-1]["click_timestamp"] if misses else None,
        }
