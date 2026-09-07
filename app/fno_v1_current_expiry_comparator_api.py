"""Durable internal API for the frozen V1-on-current-expiry comparator."""
from __future__ import annotations

import asyncio
import os
import traceback
import uuid
from datetime import datetime, timezone

from fastapi import Header, HTTPException
from psycopg.types.json import Jsonb

from .fno_v1_current_expiry_comparator import (
    PROTOCOL_ID,
    SOURCE_PROTOCOL_ID,
    architecture_contract,
    evaluate_frozen_dataset,
)

UTC = timezone.utc
SOURCE_RUN_TABLE = "fno_market_brain_v3_current_expiry_dataset_runs"
RUN_TABLE = "fno_v1_current_expiry_comparator_runs"
SQL = f"""
CREATE TABLE IF NOT EXISTS {RUN_TABLE}(
    run_id TEXT PRIMARY KEY,
    protocol_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    deployment_commit TEXT,
    status TEXT NOT NULL CHECK(status IN('RUNNING','COMPLETED','FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    progress_json JSONB,
    result_json JSONB,
    error TEXT,
    traceback TEXT
);
CREATE INDEX IF NOT EXISTS fno_v1_current_expiry_comparator_started_idx
ON {RUN_TABLE}(started_at DESC);
"""

_task = None
_task_run_id = None
_lock = None


def _connect(url):
    import psycopg
    return psycopg.connect(url, connect_timeout=10)


def _ensure_sync(url):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SQL)
        connection.commit()


async def _ensure(url):
    await asyncio.to_thread(_ensure_sync, url)


def _row(row):
    if not row:
        return None
    keys = (
        "run_id", "protocol_id", "source_run_id", "deployment_commit", "status",
        "started_at", "updated_at", "completed_at", "heartbeat_at",
        "progress_json", "result_json", "error", "traceback",
    )
    payload = dict(zip(keys, row))
    for key in ("started_at", "updated_at", "completed_at", "heartbeat_at"):
        if isinstance(payload.get(key), datetime):
            payload[key] = payload[key].astimezone(UTC).isoformat()
    return payload


def _latest_source_sync(url):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT run_id,deployment_commit,status,error FROM {SOURCE_RUN_TABLE} "
                "WHERE protocol_id=%s ORDER BY started_at DESC LIMIT 1",
                (SOURCE_PROTOCOL_ID,),
            )
            row = cursor.fetchone()
    if not row:
        return None
    return {
        "run_id": row[0],
        "deployment_commit": row[1],
        "status": row[2],
        "error": row[3],
    }


async def _latest_source(url):
    return await asyncio.to_thread(_latest_source_sync, url)


def _source_result_sync(url, run_id):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT result_json FROM {SOURCE_RUN_TABLE} "
                "WHERE run_id=%s AND protocol_id=%s AND status='COMPLETED'",
                (run_id, SOURCE_PROTOCOL_ID),
            )
            row = cursor.fetchone()
    return row[0] if row and row[0] else None


async def _source_result(url, run_id):
    return await asyncio.to_thread(_source_result_sync, url, run_id)


def _latest_sync(url):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT run_id,protocol_id,source_run_id,deployment_commit,status,started_at,updated_at,completed_at,heartbeat_at,progress_json,result_json,error,traceback "
                f"FROM {RUN_TABLE} WHERE protocol_id=%s ORDER BY started_at DESC LIMIT 1",
                (PROTOCOL_ID,),
            )
            return _row(cursor.fetchone())


async def _latest(url):
    return await asyncio.to_thread(_latest_sync, url)


def _create_sync(url, run_id, source_run_id, commit):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {RUN_TABLE}(run_id,protocol_id,source_run_id,deployment_commit,status,progress_json) "
                "VALUES(%s,%s,%s,%s,'RUNNING',%s)",
                (run_id, PROTOCOL_ID, source_run_id, commit or None, Jsonb({"stage": "QUEUED"})),
            )
        connection.commit()


async def _create(url, run_id, source_run_id, commit):
    await asyncio.to_thread(_create_sync, url, run_id, source_run_id, commit)


def _update_sync(url, run_id, progress):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {RUN_TABLE} SET updated_at=NOW(),heartbeat_at=NOW(),progress_json=%s "
                "WHERE run_id=%s AND status='RUNNING'",
                (Jsonb(dict(progress)), run_id),
            )
        connection.commit()


async def _update(url, run_id, progress):
    await asyncio.to_thread(_update_sync, url, run_id, progress)


def _complete_sync(url, run_id, result):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {RUN_TABLE} SET status='COMPLETED',result_json=%s,progress_json=%s,error=NULL,traceback=NULL,"
                "updated_at=NOW(),heartbeat_at=NOW(),completed_at=NOW() WHERE run_id=%s AND status='RUNNING'",
                (Jsonb(dict(result)), Jsonb({"stage": "COMPLETED"}), run_id),
            )
        connection.commit()


async def _complete(url, run_id, result):
    await asyncio.to_thread(_complete_sync, url, run_id, result)


def _fail_sync(url, run_id, error, trace):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {RUN_TABLE} SET status='FAILED',error=%s,traceback=%s,progress_json=%s,"
                "updated_at=NOW(),heartbeat_at=NOW(),completed_at=NOW() WHERE run_id=%s AND status='RUNNING'",
                (error, trace, Jsonb({"stage": "FAILED"}), run_id),
            )
        connection.commit()


async def _fail(url, run_id, error, trace):
    await asyncio.to_thread(_fail_sync, url, run_id, error, trace)


def _summary(run):
    if not run:
        return {"status": "IDLE", "protocol_id": PROTOCOL_ID, "safety": architecture_contract()}
    payload = {
        "run_id": run.get("run_id"),
        "protocol_id": run.get("protocol_id"),
        "source_run_id": run.get("source_run_id"),
        "deployment_commit": run.get("deployment_commit"),
        "status": run.get("status"),
        "heartbeat_at": run.get("heartbeat_at"),
        "progress": run.get("progress_json") or {},
        "error": run.get("error") if run.get("status") == "FAILED" else None,
        "safety": architecture_contract(),
    }
    result = run.get("result_json") or {}
    if run.get("status") == "COMPLETED":
        payload["source"] = result.get("source")
        payload["summary"] = result.get("summary")
        payload["decision_comparison"] = result.get("decision_comparison")
    return payload


async def _run(settings, run_id, source):
    global _task, _task_run_id
    try:
        await _update(settings.database_url, run_id, {"stage": "LOADING_FROZEN_SOURCE"})
        source_result = await _source_result(settings.database_url, source["run_id"])
        if not source_result:
            raise RuntimeError("SOURCE_DATASET_RESULT_MISSING")
        await _update(settings.database_url, run_id, {"stage": "EVALUATING_FROZEN_V1_LOGIC"})
        result = await asyncio.to_thread(evaluate_frozen_dataset, source_result)
        await _complete(settings.database_url, run_id, result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await _fail(
            settings.database_url,
            run_id,
            f"{exc.__class__.__name__}: {str(exc)[:1600]}",
            traceback.format_exc()[-16000:],
        )
    finally:
        if _task_run_id == run_id:
            _task = None
            _task_run_id = None


async def _run_deferred(settings, run_id, source):
    await asyncio.sleep(0.5)
    await _run(settings, run_id, source)


def _active(run_id=None):
    return bool(_task is not None and not _task.done() and (run_id is None or _task_run_id == run_id))


async def _worker(settings, run, source):
    global _task, _task_run_id, _lock
    if not run or run.get("status") != "RUNNING":
        return
    run_id = str(run.get("run_id") or "")
    if not run_id or _active(run_id):
        return
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _active(run_id):
            return
        _task_run_id = run_id
        _task = asyncio.create_task(_run_deferred(settings, run_id, source))


def register_fno_v1_current_expiry_comparator_routes(app, settings, collector_auth):
    if getattr(app.state, "fno_v1_current_expiry_comparator_registered", False):
        return
    app.state.fno_v1_current_expiry_comparator_registered = True

    @app.post("/v1/internal/fno/v1-current-expiry-comparator/start")
    async def start(x_collector_token: str | None = Header(default=None)):
        collector_auth(x_collector_token)
        await _ensure(settings.database_url)
        source = await _latest_source(settings.database_url)
        if not source:
            raise HTTPException(409, detail={"status": "SOURCE_DATASET_MISSING"})
        if source.get("status") != "COMPLETED":
            raise HTTPException(409, detail={"status": "SOURCE_DATASET_NOT_COMPLETED", "source": source})

        latest = await _latest(settings.database_url)
        if latest and latest.get("status") == "RUNNING":
            if latest.get("source_run_id") != source.get("run_id"):
                raise HTTPException(409, detail={"status": "OLDER_SOURCE_COMPARATOR_STILL_RUNNING"})
            await _worker(settings, latest, source)
            return _summary(latest)
        if latest and latest.get("status") == "COMPLETED" and latest.get("source_run_id") == source.get("run_id"):
            return _summary(latest)

        run_id = uuid.uuid4().hex
        await _create(settings.database_url, run_id, source["run_id"], os.getenv("RENDER_GIT_COMMIT", ""))
        created = await _latest(settings.database_url)
        await _worker(settings, created, source)
        return _summary(created)

    @app.get("/v1/internal/fno/v1-current-expiry-comparator/status")
    async def status(x_collector_token: str | None = Header(default=None)):
        collector_auth(x_collector_token)
        await _ensure(settings.database_url)
        source = await _latest_source(settings.database_url)
        run = await _latest(settings.database_url)
        if run and run.get("status") == "RUNNING" and source and source.get("run_id") == run.get("source_run_id"):
            await _worker(settings, run, source)
        return _summary(await _latest(settings.database_url))

    @app.get("/v1/internal/fno/v1-current-expiry-comparator/result")
    async def result(x_collector_token: str | None = Header(default=None)):
        collector_auth(x_collector_token)
        await _ensure(settings.database_url)
        run = await _latest(settings.database_url)
        if not run:
            raise HTTPException(409, detail={"status": "IDLE"})
        if run.get("status") == "FAILED":
            raise HTTPException(500, detail={"run_id": run.get("run_id"), "error": run.get("error"), "traceback": run.get("traceback")})
        if run.get("status") != "COMPLETED":
            raise HTTPException(409, detail=_summary(run))
        return run.get("result_json")
