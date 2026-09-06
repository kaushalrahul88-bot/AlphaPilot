"""Authenticated durable API for the frozen underlying-only random-click replay."""
from __future__ import annotations

import asyncio
import os
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import Header, HTTPException
from psycopg.types.json import Jsonb

from .fno_underlying_random_replay_v1 import PROTOCOL_ID, run_underlying_random_replay_v1

UTC = timezone.utc
RUN_TABLE = "fno_underlying_random_backtest_runs_v1"
RUN_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {RUN_TABLE} (
    run_id TEXT PRIMARY KEY,
    protocol_id TEXT NOT NULL,
    deployment_commit TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    result_json JSONB,
    error TEXT,
    traceback TEXT
);
CREATE INDEX IF NOT EXISTS fno_underlying_random_runs_started_idx
    ON {RUN_TABLE} (started_at DESC);
"""

_task: asyncio.Task | None = None
_task_run_id: str | None = None
_resume_lock: asyncio.Lock | None = None


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _ensure_schema_sync(database_url: str) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(RUN_SCHEMA_SQL)
        conn.commit()


async def _ensure_schema(database_url: str) -> None:
    await asyncio.to_thread(_ensure_schema_sync, database_url)


def _row_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = (
        "run_id", "protocol_id", "deployment_commit", "status",
        "started_at", "updated_at", "completed_at", "heartbeat_at",
        "attempt_count", "result_json", "error", "traceback",
    )
    result = dict(zip(keys, row))
    for key in ("started_at", "updated_at", "completed_at", "heartbeat_at"):
        value = result.get(key)
        if isinstance(value, datetime):
            result[key] = value.astimezone(UTC).isoformat()
    return result


def _latest_run_sync(database_url: str) -> dict[str, Any] | None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT run_id, protocol_id, deployment_commit, status,
                       started_at, updated_at, completed_at, heartbeat_at,
                       attempt_count, result_json, error, traceback
                FROM {RUN_TABLE}
                WHERE protocol_id=%s
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (PROTOCOL_ID,),
            )
            return _row_dict(cur.fetchone())


async def _latest_run(database_url: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_latest_run_sync, database_url)


def _create_run_sync(database_url: str, run_id: str, deployment_commit: str) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {RUN_TABLE}
                    (run_id, protocol_id, deployment_commit, status,
                     started_at, updated_at, heartbeat_at)
                VALUES (%s, %s, %s, 'RUNNING', NOW(), NOW(), NOW())
                """,
                (run_id, PROTOCOL_ID, deployment_commit or None),
            )
        conn.commit()


async def _create_run(database_url: str, run_id: str, deployment_commit: str) -> None:
    await asyncio.to_thread(_create_run_sync, database_url, run_id, deployment_commit)


def _mark_attempt_sync(database_url: str, run_id: str) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {RUN_TABLE}
                SET attempt_count=attempt_count+1, updated_at=NOW(), heartbeat_at=NOW()
                WHERE run_id=%s AND status='RUNNING'
                """,
                (run_id,),
            )
        conn.commit()


async def _mark_attempt(database_url: str, run_id: str) -> None:
    await asyncio.to_thread(_mark_attempt_sync, database_url, run_id)


def _complete_run_sync(database_url: str, run_id: str, result: Mapping[str, Any]) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {RUN_TABLE}
                SET status='COMPLETED', result_json=%s, error=NULL, traceback=NULL,
                    updated_at=NOW(), heartbeat_at=NOW(), completed_at=NOW()
                WHERE run_id=%s AND status='RUNNING'
                """,
                (Jsonb(dict(result)), run_id),
            )
        conn.commit()


async def _complete_run(database_url: str, run_id: str, result: Mapping[str, Any]) -> None:
    await asyncio.to_thread(_complete_run_sync, database_url, run_id, dict(result))


def _fail_run_sync(database_url: str, run_id: str, error: str, trace: str) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {RUN_TABLE}
                SET status='FAILED', error=%s, traceback=%s,
                    updated_at=NOW(), heartbeat_at=NOW(), completed_at=NOW()
                WHERE run_id=%s AND status='RUNNING'
                """,
                (error, trace, run_id),
            )
        conn.commit()


async def _fail_run(database_url: str, run_id: str, error: str, trace: str) -> None:
    await asyncio.to_thread(_fail_run_sync, database_url, run_id, error, trace)


def _summary(run: Mapping[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {
            "status": "IDLE",
            "protocol_id": PROTOCOL_ID,
            "underlying_only": True,
            "live_execution": False,
            "capital_committed": 0,
        }
    status = str(run.get("status") or "IDLE")
    base = {
        "run_id": run.get("run_id"),
        "protocol_id": run.get("protocol_id"),
        "deployment_commit": run.get("deployment_commit"),
        "status": status,
        "attempt_count": int(run.get("attempt_count") or 0),
        "heartbeat_at": run.get("heartbeat_at"),
        "error": run.get("error") if status == "FAILED" else None,
        "underlying_only": True,
        "live_execution": False,
        "capital_committed": 0,
    }
    if status == "COMPLETED":
        result = run.get("result_json") or {}
        base.update({
            "source_window": result.get("source_window"),
            "universe_size": result.get("universe_size"),
            "scheduled_clicks": result.get("scheduled_clicks"),
            "symbol_slots": result.get("symbol_slots"),
            "summary": result.get("summary"),
            "safety": result.get("safety"),
        })
    return base


async def _run(settings, run_id: str) -> None:
    global _task, _task_run_id
    try:
        await _mark_attempt(settings.database_url, run_id)
        result = await run_underlying_random_replay_v1(settings.database_url)
        if result.get("status") != "COMPLETED":
            raise RuntimeError(
                f"underlying replay did not complete: {result.get('status')} "
                f"{result.get('history_errors') or ''}"
            )
        await _complete_run(settings.database_url, run_id, result)
    except asyncio.CancelledError:
        # Leave the durable row RUNNING. A later status/start request resumes it.
        raise
    except Exception as exc:
        await _fail_run(
            settings.database_url,
            run_id,
            f"{exc.__class__.__name__}: {str(exc)[:1200]}",
            traceback.format_exc()[-12000:],
        )
    finally:
        if _task_run_id == run_id:
            _task = None
            _task_run_id = None


def _local_worker_active(run_id: str | None = None) -> bool:
    if _task is None or _task.done():
        return False
    return run_id is None or _task_run_id == run_id


async def _ensure_worker(settings, run: Mapping[str, Any] | None) -> None:
    global _task, _task_run_id, _resume_lock
    if not run or run.get("status") != "RUNNING":
        return
    run_id = str(run.get("run_id") or "")
    if not run_id or _local_worker_active(run_id):
        return
    if _resume_lock is None:
        _resume_lock = asyncio.Lock()
    async with _resume_lock:
        if _local_worker_active(run_id):
            return
        _task_run_id = run_id
        _task = asyncio.create_task(_run(settings, run_id))


def register_fno_underlying_random_backtest_routes(app, settings, collector_auth) -> None:
    @app.post("/v1/internal/fno/underlying-random-backtest/start")
    async def start_underlying_random_backtest(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        await _ensure_schema(settings.database_url)
        latest = await _latest_run(settings.database_url)
        if latest and latest.get("status") == "RUNNING":
            await _ensure_worker(settings, latest)
            return _summary(latest)

        run_id = uuid.uuid4().hex
        deployment_commit = os.getenv("RENDER_GIT_COMMIT", "")
        await _create_run(settings.database_url, run_id, deployment_commit)
        created = await _latest_run(settings.database_url)
        await _ensure_worker(settings, created)
        return _summary(created)

    @app.get("/v1/internal/fno/underlying-random-backtest/status")
    async def underlying_random_backtest_status(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        await _ensure_schema(settings.database_url)
        run = await _latest_run(settings.database_url)
        await _ensure_worker(settings, run)
        return _summary(await _latest_run(settings.database_url))

    @app.get("/v1/internal/fno/underlying-random-backtest/result")
    async def underlying_random_backtest_result(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        await _ensure_schema(settings.database_url)
        run = await _latest_run(settings.database_url)
        await _ensure_worker(settings, run)
        if not run:
            raise HTTPException(status_code=409, detail={"status": "IDLE"})
        if run.get("status") == "FAILED":
            raise HTTPException(
                status_code=500,
                detail={
                    "run_id": run.get("run_id"),
                    "error": run.get("error"),
                    "traceback": run.get("traceback"),
                },
            )
        if run.get("status") != "COMPLETED":
            raise HTTPException(
                status_code=409,
                detail={"run_id": run.get("run_id"), "status": run.get("status")},
            )
        return run.get("result_json") or {}


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_UNDERLYING_RANDOM_BACKTEST_API_V1",
        "collector_auth_required": True,
        "durable_run_state": True,
        "automatic_resume_after_process_restart": True,
        "source": "existing durable historical candle cache only",
        "option_chain_read": False,
        "live_execution": False,
        "capital_committed": 0,
    }
