"""Persistent history for user-triggered BTC underlying backtests.

The dashboard job runner keeps live progress in memory for responsiveness, while
this store writes durable run metadata and terminal results to Postgres so
completed/failed backtests remain accessible after refreshes and deploys.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

TABLE_NAME = "crypto_btc_underlying_backtest_runs_v1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    phase TEXT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NULL,
    completed_clicks INTEGER NOT NULL DEFAULT 0,
    total_clicks INTEGER NOT NULL DEFAULT 96,
    window_start TIMESTAMPTZ NULL,
    window_end_exclusive TIMESTAMPTZ NULL,
    scheduled_clicks INTEGER NULL,
    summary JSONB NULL,
    result JSONB NULL,
    error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS crypto_btc_underlying_backtest_runs_finished_idx
    ON {TABLE_NAME} (finished_at DESC NULLS LAST, started_at DESC);
"""

INSERT_RUNNING_SQL = f"""
INSERT INTO {TABLE_NAME} (
    job_id, status, phase, started_at, completed_clicks, total_clicks
) VALUES (%s, 'RUNNING', %s, %s, %s, %s)
ON CONFLICT (job_id) DO NOTHING;
"""

COMPLETE_SQL = f"""
UPDATE {TABLE_NAME}
SET status = 'COMPLETED',
    phase = 'COMPLETED',
    finished_at = %s,
    completed_clicks = %s,
    total_clicks = %s,
    window_start = %s,
    window_end_exclusive = %s,
    scheduled_clicks = %s,
    summary = %s::jsonb,
    result = %s::jsonb,
    error = NULL,
    updated_at = NOW()
WHERE job_id = %s;
"""

FAIL_SQL = f"""
UPDATE {TABLE_NAME}
SET status = 'FAILED',
    phase = 'FAILED',
    finished_at = %s,
    completed_clicks = %s,
    total_clicks = %s,
    error = %s,
    updated_at = NOW()
WHERE job_id = %s;
"""

SELECT_JOB_SQL = f"""
SELECT job_id, status, phase, started_at, finished_at,
       completed_clicks, total_clicks, window_start, window_end_exclusive,
       scheduled_clicks, summary, result, error
FROM {TABLE_NAME}
WHERE job_id = %s;
"""

LIST_JOBS_SQL = f"""
SELECT job_id, status, phase, started_at, finished_at,
       completed_clicks, total_clicks, window_start, window_end_exclusive,
       scheduled_clicks, summary, NULL::jsonb AS result, error
FROM {TABLE_NAME}
ORDER BY COALESCE(finished_at, started_at) DESC, started_at DESC
LIMIT %s;
"""

_COLUMNS = (
    "job_id", "status", "phase", "started_at", "finished_at",
    "completed_clicks", "total_clicks", "window_start", "window_end_exclusive",
    "scheduled_clicks", "summary", "result", "error",
)


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _decode_json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return value


def _iso(value: Any) -> str | None:
    dt = _parse_datetime(value)
    return None if dt is None else dt.astimezone(timezone.utc).isoformat()


def compact_backtest_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the dashboard scorecard without the potentially large click tape."""
    if not isinstance(result, dict):
        return None
    return {
        "status": result.get("status"),
        "window_start": result.get("window_start"),
        "window_end_exclusive": result.get("window_end_exclusive"),
        "scheduled_clicks": result.get("scheduled_clicks"),
        "summary": result.get("summary"),
    }


def _row_to_job(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    values = dict(zip(_COLUMNS, row, strict=True))
    summary = _decode_json(values.get("summary"))
    result = _decode_json(values.get("result"))
    if result is None and values.get("status") == "COMPLETED":
        result = {
            "status": "COMPLETED",
            "window_start": _iso(values.get("window_start")),
            "window_end_exclusive": _iso(values.get("window_end_exclusive")),
            "scheduled_clicks": values.get("scheduled_clicks"),
            "summary": summary,
        }
    completed = int(values.get("completed_clicks") or 0)
    total = int(values.get("total_clicks") or 0)
    return {
        "job_id": str(values["job_id"]),
        "status": str(values["status"]),
        "phase": values.get("phase"),
        "completed_clicks": completed,
        "total_clicks": total,
        "progress_pct": round(completed / total * 100.0, 1) if total else 0.0,
        "started_at": _iso(values.get("started_at")),
        "finished_at": _iso(values.get("finished_at")),
        "error": values.get("error"),
        "result": result,
        "history_persisted": True,
    }


class PostgresBtcBacktestHistoryStore:
    """Durable Postgres history for BTC dashboard backtests."""

    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required for BTC backtest history")

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    async def create_job(self, job: dict[str, Any]) -> None:
        await asyncio.to_thread(self._create_job_sync, job)

    def _create_job_sync(self, job: dict[str, Any]) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    INSERT_RUNNING_SQL,
                    (
                        str(job["job_id"]),
                        str(job.get("phase") or "QUEUED"),
                        _parse_datetime(job.get("started_at")) or datetime.now(timezone.utc),
                        int(job.get("completed_clicks") or 0),
                        int(job.get("total_clicks") or 96),
                    ),
                )
            conn.commit()

    async def save_completed(self, job: dict[str, Any], result: dict[str, Any]) -> None:
        await asyncio.to_thread(self._save_completed_sync, job, result)

    def _save_completed_sync(self, job: dict[str, Any], result: dict[str, Any]) -> None:
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else None
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    COMPLETE_SQL,
                    (
                        _parse_datetime(job.get("finished_at")) or datetime.now(timezone.utc),
                        int(job.get("completed_clicks") or job.get("total_clicks") or 96),
                        int(job.get("total_clicks") or 96),
                        _parse_datetime(result.get("window_start")),
                        _parse_datetime(result.get("window_end_exclusive")),
                        int(result.get("scheduled_clicks") or 0),
                        None if summary is None else _json_text(summary),
                        _json_text(result),
                        str(job["job_id"]),
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("BTC backtest history row disappeared before completion")
            conn.commit()

    async def save_failed(self, job: dict[str, Any]) -> None:
        await asyncio.to_thread(self._save_failed_sync, job)

    def _save_failed_sync(self, job: dict[str, Any]) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    FAIL_SQL,
                    (
                        _parse_datetime(job.get("finished_at")) or datetime.now(timezone.utc),
                        int(job.get("completed_clicks") or 0),
                        int(job.get("total_clicks") or 96),
                        str(job.get("error") or "BTC underlying backtest failed")[:1000],
                        str(job["job_id"]),
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("BTC backtest history row disappeared before failure persistence")
            conn.commit()

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_job_sync, job_id)

    def _get_job_sync(self, job_id: str) -> dict[str, Any] | None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_JOB_SQL, (str(job_id),))
                row = cur.fetchone()
        return _row_to_job(row)

    async def list_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        return await asyncio.to_thread(self._list_jobs_sync, safe_limit)

    def _list_jobs_sync(self, limit: int) -> list[dict[str, Any]]:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(LIST_JOBS_SQL, (limit,))
                rows = cur.fetchall()
        return [job for row in rows if (job := _row_to_job(row)) is not None]


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "BTC_DASHBOARD_BACKTEST_HISTORY_V1",
        "backend": "POSTGRES",
        "completed_runs_persisted": True,
        "failed_runs_persisted": True,
        "survives_browser_refresh": True,
        "survives_api_deploy": True,
        "full_terminal_result_retained": True,
        "history_list_uses_compact_scorecards": True,
    }
