"""Authenticated operational wrapper for the read-only F&O 15-minute replay."""
from __future__ import annotations

import asyncio
import traceback
from typing import Any

from fastapi import Header, HTTPException

from .fno_15m_historical_replay_v1 import run_fno_15m_historical_replay
from .providers.factory import get_provider


_job: dict[str, Any] = {"status": "IDLE", "result": None, "error": None}
_task: asyncio.Task | None = None


def _summary(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "IDLE")
    if status != "COMPLETED":
        return {
            "status": status,
            "error": job.get("error") if status == "FAILED" else None,
            "diagnostic_only": True,
            "live_execution": False,
            "capital_committed": 0,
        }
    result = job.get("result") or {}
    return {
        "status": "COMPLETED",
        "mode": result.get("mode"),
        "coverage": result.get("coverage"),
        "strict_summary": ((result.get("results") or {}).get("STRICT_V2") or {}).get("summary"),
        "diagnostic_summary": ((result.get("results") or {}).get("COVERAGE_30M") or {}).get("summary"),
        "safety": result.get("safety"),
    }


async def _run(settings) -> None:
    global _job
    try:
        result = await run_fno_15m_historical_replay(
            get_provider(settings),
            settings.database_url,
        )
        _job = {"status": "COMPLETED", "result": result, "error": None}
    except Exception as exc:
        _job = {
            "status": "FAILED",
            "result": None,
            "error": f"{exc.__class__.__name__}: {str(exc)[:1200]}",
            "traceback": traceback.format_exc()[-8000:],
        }


def register_fno_15m_backtest_routes(app, settings, collector_auth) -> None:
    @app.post("/v1/internal/fno/backtest-15m/start")
    async def start_fno_15m_backtest(
        x_collector_token: str | None = Header(default=None),
    ):
        global _job, _task
        collector_auth(x_collector_token)
        if _job.get("status") == "RUNNING":
            return _summary(_job)
        _job = {"status": "RUNNING", "result": None, "error": None}
        _task = asyncio.create_task(_run(settings))
        return _summary(_job)

    @app.get("/v1/internal/fno/backtest-15m/status")
    async def fno_15m_backtest_status(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        return _summary(_job)

    @app.get("/v1/internal/fno/backtest-15m/result")
    async def fno_15m_backtest_result(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        if _job.get("status") == "FAILED":
            raise HTTPException(
                status_code=500,
                detail={
                    "error": _job.get("error"),
                    "traceback": _job.get("traceback"),
                },
            )
        if _job.get("status") != "COMPLETED":
            raise HTTPException(
                status_code=409,
                detail={"status": _job.get("status", "IDLE")},
            )
        return _job.get("result") or {}


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_15M_BACKTEST_API_V1",
        "collector_auth_required": True,
        "background_job": True,
        "database_writes": False,
        "strategy_policy_changed": False,
        "live_execution": False,
        "capital_committed": 0,
    }
