"""Authenticated restart-safe wrapper for the read-only F&O 15-minute replay."""
from __future__ import annotations

import asyncio
import os
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import Header, HTTPException
from psycopg.types.json import Jsonb

from .fno_15m_historical_replay_v1 import MODE
from .fno_15m_restart_safe_replay import run_fno_15m_historical_replay_restart_safe
from .providers.factory import get_provider

UTC = timezone.utc
RUN_TABLE = "fno_15m_backtest_runs_v2"
RUN_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {RUN_TABLE} (
    run_id TEXT PRIMARY KEY,
    methodology_id TEXT NOT NULL,
    deployment_commit TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    progress_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    result_json JSONB,
    error TEXT,
    traceback TEXT
);
CREATE INDEX IF NOT EXISTS fno_15m_backtest_runs_v2_started_idx
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
        "run_id",
        "methodology_id",
        "deployment_commit",
        "status",
        "started_at",
        "updated_at",
        "completed_at",
        "heartbeat_at",
        "attempt_count",
        "progress_json",
        "result_json",
        "error",
        "traceback",
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
                SELECT run_id, methodology_id, deployment_commit, status,
                       started_at, updated_at, completed_at, heartbeat_at,
                       attempt_count, progress_json, result_json, error, traceback
                FROM {RUN_TABLE}
                WHERE methodology_id=%s
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (MODE,),
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
                    (run_id, methodology_id, deployment_commit, status,
                     started_at, updated_at, heartbeat_at, progress_json)
                VALUES (%s, %s, %s, 'RUNNING', NOW(), NOW(), NOW(), %s)
                """,
                (
                    run_id,
                    MODE,
                    deployment_commit or None,
                    Jsonb({"stage": "QUEUED", "restart_safe": True}),
                ),
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
                SET attempt_count=attempt_count+1,
                    updated_at=NOW(), heartbeat_at=NOW(),
                    progress_json = progress_json || %s::jsonb
                WHERE run_id=%s AND status='RUNNING'
                """,
                (Jsonb({"stage": "STARTING_OR_RESUMING_WORKER"}), run_id),
            )
        conn.commit()


async def _mark_attempt(database_url: str, run_id: str) -> None:
    await asyncio.to_thread(_mark_attempt_sync, database_url, run_id)


def _save_progress_sync(database_url: str, run_id: str, progress: Mapping[str, Any]) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {RUN_TABLE}
                SET progress_json=%s, updated_at=NOW(), heartbeat_at=NOW()
                WHERE run_id=%s AND status='RUNNING'
                """,
                (Jsonb(dict(progress)), run_id),
            )
        conn.commit()


async def _save_progress(database_url: str, run_id: str, progress: Mapping[str, Any]) -> None:
    await asyncio.to_thread(_save_progress_sync, database_url, run_id, dict(progress))


def _complete_run_sync(database_url: str, run_id: str, result: Mapping[str, Any]) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {RUN_TABLE}
                SET status='COMPLETED', result_json=%s,
                    error=NULL, traceback=NULL,
                    progress_json=%s,
                    updated_at=NOW(), heartbeat_at=NOW(), completed_at=NOW()
                WHERE run_id=%s AND status='RUNNING'
                """,
                (
                    Jsonb(dict(result)),
                    Jsonb({"stage": "COMPLETED", "restart_safe": True}),
                    run_id,
                ),
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
                    progress_json=%s,
                    updated_at=NOW(), heartbeat_at=NOW(), completed_at=NOW()
                WHERE run_id=%s AND status='RUNNING'
                """,
                (
                    error,
                    trace,
                    Jsonb({"stage": "FAILED", "restart_safe": True}),
                    run_id,
                ),
            )
        conn.commit()


async def _fail_run(database_url: str, run_id: str, error: str, trace: str) -> None:
    await asyncio.to_thread(_fail_run_sync, database_url, run_id, error, trace)


def _summary(run: Mapping[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {
            "status": "IDLE",
            "error": None,
            "diagnostic_only": True,
            "live_execution": False,
            "capital_committed": 0,
            "restart_safe": True,
        }
    status = str(run.get("status") or "IDLE")
    base = {
        "run_id": run.get("run_id"),
        "status": status,
        "error": run.get("error") if status == "FAILED" else None,
        "progress": run.get("progress_json") or {},
        "attempt_count": int(run.get("attempt_count") or 0),
        "heartbeat_at": run.get("heartbeat_at"),
        "deployment_commit": run.get("deployment_commit"),
        "diagnostic_only": True,
        "live_execution": False,
        "capital_committed": 0,
        "restart_safe": True,
    }
    if status == "COMPLETED":
        result = run.get("result_json") or {}
        base.update({
            "mode": result.get("mode"),
            "coverage": result.get("coverage"),
            "strict_summary": ((result.get("results") or {}).get("STRICT_V2") or {}).get("summary"),
            "diagnostic_summary": ((result.get("results") or {}).get("COVERAGE_30M") or {}).get("summary"),
            "safety": result.get("safety"),
        })
    return base


async def _run(settings, run_id: str) -> None:
    global _task, _task_run_id
    database_url = settings.database_url
    try:
        await _mark_attempt(database_url, run_id)

        async def progress_callback(progress: Mapping[str, Any]) -> None:
            await _save_progress(database_url, run_id, progress)

        result = await run_fno_15m_historical_replay_restart_safe(
            get_provider(settings),
            database_url,
            progress_callback=progress_callback,
        )
        await _complete_run(database_url, run_id, result)
    except asyncio.CancelledError:
        # A process shutdown must leave the durable row RUNNING. The next status
        # poll on the replacement process will resume the same run.
        try:
            await _save_progress(
                database_url,
                run_id,
                {"stage": "PROCESS_INTERRUPTED_AWAITING_RESUME", "restart_safe": True},
            )
        except Exception:
            pass
        raise
    except Exception as exc:
        await _fail_run(
            database_url,
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


def register_fno_15m_backtest_routes(app, settings, collector_auth) -> None:
    @app.post("/v1/internal/fno/backtest-15m/start")
    async def start_fno_15m_backtest(
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

    @app.get("/v1/internal/fno/backtest-15m/status")
    async def fno_15m_backtest_status(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        await _ensure_schema(settings.database_url)
        run = await _latest_run(settings.database_url)
        await _ensure_worker(settings, run)
        # Re-read after scheduling so callers get the freshest durable heartbeat.
        return _summary(await _latest_run(settings.database_url))

    @app.get("/v1/internal/fno/backtest-15m/result")
    async def fno_15m_backtest_result(
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
                detail={
                    "run_id": run.get("run_id"),
                    "status": run.get("status"),
                    "progress": run.get("progress_json") or {},
                },
            )
        return run.get("result_json") or {}


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_15M_BACKTEST_API_V2_RESTART_SAFE",
        "collector_auth_required": True,
        "background_job": True,
        "durable_run_state": True,
        "durable_result": True,
        "automatic_resume_after_process_restart": True,
        "durable_reconstructible_candle_checkpoints": True,
        "point_in_time_option_chain_writes": False,
        "orchestration_database_writes": True,
        "strategy_policy_changed": False,
        "live_execution": False,
        "capital_committed": 0,
    }
