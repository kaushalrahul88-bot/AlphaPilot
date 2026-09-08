"""Durable internal API for frozen V5 Historical Holdout A dataset build."""
from __future__ import annotations

import asyncio
import json
import os
import traceback
import uuid
import zlib
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException
from psycopg.types.json import Jsonb

from .fno_candle_only_symbol_alias_v1 import current_cash_symbol_aliases
from .fno_market_brain_v5_holdout_a_dataset import (
    PROTOCOL_ID,
    architecture_contract,
    build_holdout_a_dataset,
)
from .providers.factory import get_provider

UTC = timezone.utc
RUN_TABLE = "fno_market_brain_v5_holdout_a_dataset_runs"
CACHE_TABLE = "fno_market_brain_v5_holdout_a_history_segments"
STALE_AFTER = timedelta(minutes=10)
DB_RETRY_DELAYS_SECONDS = (0.25, 0.75, 2.0, 5.0)
FINAL_DB_RETRY_DELAYS_SECONDS = (1.0, 2.0, 5.0, 10.0, 15.0) + (30.0,) * 8
RESULT_BLOB_CODEC = "zlib-json-v1"

SQL = f"""CREATE TABLE IF NOT EXISTS {RUN_TABLE}(
run_id TEXT PRIMARY KEY,
protocol_id TEXT NOT NULL,
deployment_commit TEXT,
status TEXT NOT NULL CHECK(status IN('RUNNING','COMPLETED','FAILED')),
started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
completed_at TIMESTAMPTZ,
heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
attempt_count INTEGER NOT NULL DEFAULT 0,
progress_json JSONB,
result_json JSONB,
error TEXT,
traceback TEXT);
ALTER TABLE {RUN_TABLE} ADD COLUMN IF NOT EXISTS result_blob BYTEA;
CREATE INDEX IF NOT EXISTS fno_v5_holdout_a_dataset_started_idx
ON {RUN_TABLE}(started_at DESC);
CREATE TABLE IF NOT EXISTS {CACHE_TABLE}(
protocol_id TEXT NOT NULL,
symbol TEXT NOT NULL,
timeframe TEXT NOT NULL,
window_start TIMESTAMPTZ NOT NULL,
window_end TIMESTAMPTZ NOT NULL,
rows_json JSONB NOT NULL,
row_count INTEGER NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY(protocol_id,symbol,timeframe,window_start,window_end));
CREATE INDEX IF NOT EXISTS fno_v5_holdout_a_history_symbol_idx
ON {CACHE_TABLE}(protocol_id,symbol,timeframe);"""

_task = None
_task_run_id = None
_lock = None


def _connect(url):
    import psycopg
    return psycopg.connect(url, connect_timeout=10)


def _transient_db_error(exc: Exception) -> bool:
    import psycopg
    return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))


async def _db_call(func, *args, retry_delays=None):
    delays = tuple(DB_RETRY_DELAYS_SECONDS if retry_delays is None else retry_delays)
    last = None
    attempts = len(delays) + 1
    for attempt in range(attempts):
        try:
            return await asyncio.to_thread(func, *args)
        except Exception as exc:
            last = exc
            if not _transient_db_error(exc) or attempt >= attempts - 1:
                raise
            await asyncio.sleep(delays[attempt])
    raise last  # pragma: no cover


def _ensure_sync(url):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SQL)
        connection.commit()


async def _ensure(url):
    await _db_call(_ensure_sync, url)


def _row(row):
    if not row:
        return None
    keys = (
        "run_id", "protocol_id", "deployment_commit", "status", "started_at",
        "updated_at", "completed_at", "heartbeat_at", "attempt_count",
        "progress_json", "result_json", "error", "traceback",
    )
    payload = dict(zip(keys, row))
    for key in ("started_at", "updated_at", "completed_at", "heartbeat_at"):
        if isinstance(payload.get(key), datetime):
            payload[key] = payload[key].astimezone(UTC).isoformat()
    return payload


def _latest_sync(url):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT run_id,protocol_id,deployment_commit,status,started_at,
                updated_at,completed_at,heartbeat_at,attempt_count,progress_json,
                result_json,error,traceback FROM {RUN_TABLE}
                WHERE protocol_id=%s ORDER BY started_at DESC LIMIT 1""",
                (PROTOCOL_ID,),
            )
            return _row(cursor.fetchone())


async def _latest(url):
    return await _db_call(_latest_sync, url)


def _create_sync(url, run_id, commit):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {RUN_TABLE}
                (run_id,protocol_id,deployment_commit,status,progress_json)
                VALUES(%s,%s,%s,'RUNNING',%s)""",
                (run_id, PROTOCOL_ID, commit or None, Jsonb({"stage": "QUEUED"})),
            )
        connection.commit()


async def _create(url, run_id, commit):
    await _db_call(_create_sync, url, run_id, commit)


def _update_sync(url, run_id, progress, increment=False):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            if increment:
                cursor.execute(
                    f"""UPDATE {RUN_TABLE}
                    SET attempt_count=attempt_count+1,updated_at=NOW(),
                    heartbeat_at=NOW(),progress_json=%s
                    WHERE run_id=%s AND status='RUNNING'""",
                    (Jsonb(dict(progress)), run_id),
                )
            else:
                cursor.execute(
                    f"""UPDATE {RUN_TABLE}
                    SET updated_at=NOW(),heartbeat_at=NOW(),progress_json=%s
                    WHERE run_id=%s AND status='RUNNING'""",
                    (Jsonb(dict(progress)), run_id),
                )
        connection.commit()


async def _update(url, run_id, progress, increment=False):
    await _db_call(_update_sync, url, run_id, progress, increment)


def _result_summary(result, *, blob_bytes=None):
    summary = {
        "status": result.get("status"),
        "protocol_id": result.get("protocol_id"),
        "brain_protocol_id": result.get("brain_protocol_id"),
        "brain_frozen_commit": result.get("brain_frozen_commit"),
        "experiment": result.get("experiment"),
        "decision_counts": result.get("decision_counts"),
        "data_coverage": result.get("data_coverage"),
        "context_coverage": result.get("context_coverage"),
        "frozen_5m_tape_hashes": result.get("frozen_5m_tape_hashes"),
        "safety": result.get("safety"),
        "result_blob_codec": RESULT_BLOB_CODEC,
    }
    if blob_bytes is not None:
        summary["result_blob_bytes"] = int(blob_bytes)
    return summary


def _encode_result(result):
    raw = json.dumps(
        result,
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return zlib.compress(raw, level=6)


def _decode_result(blob):
    if blob is None:
        return None
    raw = zlib.decompress(bytes(blob))
    return json.loads(raw.decode("utf-8"))


def _complete_sync(url, run_id, result):
    blob = _encode_result(result)
    summary = _result_summary(result, blob_bytes=len(blob))
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {RUN_TABLE}
                SET status='COMPLETED',result_json=%s,result_blob=%s,
                progress_json=%s,error=NULL,traceback=NULL,updated_at=NOW(),
                heartbeat_at=NOW(),completed_at=NOW()
                WHERE run_id=%s AND status='RUNNING'""",
                (Jsonb(summary), blob, Jsonb({"stage": "COMPLETED"}), run_id),
            )
        connection.commit()


async def _complete(url, run_id, result):
    await _db_call(
        _complete_sync,
        url,
        run_id,
        result,
        retry_delays=FINAL_DB_RETRY_DELAYS_SECONDS,
    )


def _full_result_sync(url, run_id):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT result_blob,result_json FROM {RUN_TABLE} WHERE run_id=%s",
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            blob, summary = row
            if blob is not None:
                return _decode_result(blob)
            return summary


async def _full_result(url, run_id):
    return await _db_call(_full_result_sync, url, run_id)


def _fail_sync(url, run_id, error, trace, result=None):
    failure_summary = None
    if result:
        failure_summary = {
            "status": result.get("status"),
            "protocol_id": result.get("protocol_id"),
            "safety": result.get("safety"),
        }
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {RUN_TABLE}
                SET status='FAILED',result_json=%s,error=%s,traceback=%s,
                progress_json=%s,updated_at=NOW(),heartbeat_at=NOW(),
                completed_at=NOW() WHERE run_id=%s AND status='RUNNING'""",
                (
                    Jsonb(failure_summary) if failure_summary else None,
                    error,
                    trace,
                    Jsonb({"stage": "FAILED"}),
                    run_id,
                ),
            )
        connection.commit()


async def _fail(url, run_id, error, trace, result=None):
    await _db_call(
        _fail_sync,
        url,
        run_id,
        error,
        trace,
        result,
        retry_delays=FINAL_DB_RETRY_DELAYS_SECONDS,
    )


def _interrupt_sync(url, run_id, reason):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {RUN_TABLE}
                SET status='FAILED',error=%s,traceback=NULL,progress_json=%s,
                updated_at=NOW(),heartbeat_at=NOW(),completed_at=NOW()
                WHERE run_id=%s AND status='RUNNING'""",
                (reason, Jsonb({"stage": "INTERRUPTED"}), run_id),
            )
        connection.commit()


async def _interrupt(url, run_id, reason):
    await _db_call(_interrupt_sync, url, run_id, reason)


def _cache_get_sync(url, symbol, timeframe, window_start, window_end):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT rows_json FROM {CACHE_TABLE}
                WHERE protocol_id=%s AND symbol=%s AND timeframe=%s
                AND window_start=%s AND window_end=%s""",
                (PROTOCOL_ID, symbol, timeframe, window_start, window_end),
            )
            row = cursor.fetchone()
            if not row:
                return None
            rows = row[0]
            return list(rows) if isinstance(rows, list) else None


async def _cache_get(url, symbol, timeframe, window_start, window_end):
    return await _db_call(
        _cache_get_sync, url, symbol, timeframe, window_start, window_end
    )


def _cache_put_sync(url, symbol, timeframe, window_start, window_end, rows):
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {CACHE_TABLE}
                (protocol_id,symbol,timeframe,window_start,window_end,rows_json,row_count)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(protocol_id,symbol,timeframe,window_start,window_end)
                DO UPDATE SET rows_json=EXCLUDED.rows_json,row_count=EXCLUDED.row_count,
                updated_at=NOW()""",
                (
                    PROTOCOL_ID, symbol, timeframe, window_start, window_end,
                    Jsonb(list(rows)), len(rows),
                ),
            )
        connection.commit()


async def _cache_put(url, symbol, timeframe, window_start, window_end, rows):
    await _db_call(
        _cache_put_sync, url, symbol, timeframe, window_start, window_end, rows
    )


def _summary(run):
    if not run:
        return {"status": "IDLE", "protocol_id": PROTOCOL_ID, "safety": architecture_contract()}
    run_id = str(run.get("run_id") or "")
    payload = {
        "run_id": run.get("run_id"),
        "protocol_id": run.get("protocol_id"),
        "deployment_commit": run.get("deployment_commit"),
        "status": run.get("status"),
        "attempt_count": int(run.get("attempt_count") or 0),
        "heartbeat_at": run.get("heartbeat_at"),
        "progress": run.get("progress_json") or {},
        "worker_active": _active(run_id) if run_id else False,
        "error": run.get("error") if run.get("status") == "FAILED" else None,
        "safety": architecture_contract(),
    }
    result = run.get("result_json") or {}
    if run.get("status") == "COMPLETED":
        payload["experiment"] = result.get("experiment")
        payload["decision_counts"] = result.get("decision_counts")
        payload["data_coverage"] = result.get("data_coverage")
        payload["context_coverage"] = result.get("context_coverage")
        payload["frozen_5m_tape_hashes"] = result.get("frozen_5m_tape_hashes")
        payload["result_blob_bytes"] = result.get("result_blob_bytes")
    elif run.get("status") == "FAILED" and result:
        payload["failure_detail"] = result
    return payload


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_stale(run, *, now=None):
    if not run or run.get("status") != "RUNNING":
        return False
    heartbeat = _parse_datetime(run.get("heartbeat_at"))
    if heartbeat is None:
        return True
    current = now or datetime.now(UTC)
    return current - heartbeat > STALE_AFTER


async def _run(settings, run_id):
    global _task, _task_run_id
    result = None
    try:
        await _update(settings.database_url, run_id, {"stage": "STARTING"}, increment=True)
        provider = get_provider(settings)

        async def progress(update):
            # A transient heartbeat failure must not abort or alter the dataset.
            try:
                await _update(settings.database_url, run_id, update)
            except Exception:
                return

        async def cache_get(symbol, timeframe, window_start, window_end):
            return await _cache_get(
                settings.database_url, symbol, timeframe, window_start, window_end
            )

        async def cache_put(symbol, timeframe, window_start, window_end, rows):
            await _cache_put(
                settings.database_url, symbol, timeframe, window_start, window_end, rows
            )

        with current_cash_symbol_aliases(provider):
            result = await build_holdout_a_dataset(
                provider,
                progress=progress,
                cache_get=cache_get,
                cache_put=cache_put,
            )
        if result.get("status") != "COMPLETED":
            raise RuntimeError(
                "V5 Holdout A dataset build did not complete: "
                f"{result.get('status')} "
                f"{result.get('history_errors') or result.get('missing_required') or ''}"
            )
        try:
            await _update(settings.database_url, run_id, {"stage": "PERSISTING_FROZEN_DATASET"})
        except Exception:
            pass
        try:
            await _complete(settings.database_url, run_id, result)
        except Exception:
            # The complete frozen result stays unscored. Explicit POST /start can
            # resume from durable history cache if persistence remains unavailable.
            return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        try:
            await _fail(
                settings.database_url,
                run_id,
                f"{exc.__class__.__name__}: {str(exc)[:1600]}",
                traceback.format_exc()[-16000:],
                result,
            )
        except Exception:
            # If the DB itself is unavailable, leave RUNNING; explicit POST /start
            # will later classify it stale and create/resume safely from cache.
            pass
    finally:
        if _task_run_id == run_id:
            _task = None
            _task_run_id = None


async def _run_deferred(settings, run_id):
    await asyncio.sleep(1.0)
    await _run(settings, run_id)


def _active(run_id=None):
    return bool(_task is not None and not _task.done() and (run_id is None or _task_run_id == run_id))


async def _worker(settings, run):
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
        _task = asyncio.create_task(_run_deferred(settings, run_id))


def register_fno_market_brain_v5_holdout_a_dataset_routes(app, settings, collector_auth):
    if getattr(app.state, "fno_market_brain_v5_holdout_a_dataset_registered", False):
        return
    app.state.fno_market_brain_v5_holdout_a_dataset_registered = True

    @app.post("/v1/internal/fno/v5-holdout-a-dataset/start")
    async def start(x_collector_token: str | None = Header(default=None)):
        collector_auth(x_collector_token)
        await _ensure(settings.database_url)
        latest = await _latest(settings.database_url)
        current_commit = os.getenv("RENDER_GIT_COMMIT", "")

        if latest and latest.get("status") == "RUNNING":
            same_commit = (latest.get("deployment_commit") or "") == current_commit
            if _active(str(latest.get("run_id") or "")):
                return _summary(latest)
            if same_commit and not _is_stale(latest):
                # Explicit POST is allowed to resume a recent interrupted worker.
                await _worker(settings, latest)
                return _summary(latest)
            reason = (
                "INTERRUPTED_STALE_RUN_REPLACED"
                if same_commit else "INTERRUPTED_DEPLOYMENT_CHANGED"
            )
            await _interrupt(settings.database_url, latest["run_id"], reason)

        run_id = uuid.uuid4().hex
        await _create(settings.database_url, run_id, current_commit)
        created = await _latest(settings.database_url)
        await _worker(settings, created)
        return _summary(created)

    @app.get("/v1/internal/fno/v5-holdout-a-dataset/status")
    async def status(x_collector_token: str | None = Header(default=None)):
        collector_auth(x_collector_token)
        await _ensure(settings.database_url)
        # Read-only by design: polling can never relaunch the expensive build.
        return _summary(await _latest(settings.database_url))

    @app.get("/v1/internal/fno/v5-holdout-a-dataset/result")
    async def result(x_collector_token: str | None = Header(default=None)):
        collector_auth(x_collector_token)
        await _ensure(settings.database_url)
        run = await _latest(settings.database_url)
        if not run:
            raise HTTPException(409, detail={"status": "IDLE"})
        if run.get("status") == "FAILED":
            raise HTTPException(500, detail={"run_id": run.get("run_id"), "error": run.get("error"), "failure_detail": run.get("result_json"), "traceback": run.get("traceback")})
        if run.get("status") != "COMPLETED":
            raise HTTPException(409, detail=_summary(run))
        payload = await _full_result(settings.database_url, run["run_id"])
        if payload is None:
            raise HTTPException(500, detail={"run_id": run.get("run_id"), "error": "COMPLETED_RESULT_MISSING"})
        return payload
