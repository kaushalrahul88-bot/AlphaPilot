"""User-triggered BTC research actions for the Crypto dashboard.

These endpoints create research/shadow artifacts only. They never accept broker
credentials, place orders, invoke a Futures route, or commit capital.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, Query, Response

from app.crypto_btc_backtest_history import PostgresBtcBacktestHistoryStore
from app.crypto_btc_first24h_underlying_backtest import run_first24h_underlying_15m
from app.crypto_btc_live_shadow_click import run_explicit_live_shadow_click
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig

_backtest_lock = asyncio.Lock()
_live_click_lock = asyncio.Lock()
_backtest_jobs: dict[str, dict] = {}
_MAX_IN_MEMORY_BACKTEST_JOBS = 20


def _database_url(settings) -> str:
    value = str(getattr(settings, "database_url", "") or "").strip()
    if not value:
        raise HTTPException(status_code=503, detail="Crypto research database is unavailable")
    return value


def _request_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"dashboard-{stamp}-{uuid4().hex[:10]}"


def _public_job(job: dict) -> dict:
    completed, total = int(job.get("completed_clicks", 0)), int(job.get("total_clicks", 96))
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "phase": job.get("phase"),
        "completed_clicks": completed,
        "total_clicks": total,
        "progress_pct": round(completed / total * 100.0, 1) if total else 0.0,
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
        "result": job.get("result"),
        "history_persisted": job.get("history_persisted"),
        "history_error": job.get("history_error"),
    }


def _remember_in_memory(job: dict) -> None:
    _backtest_jobs[str(job["job_id"])] = job
    if len(_backtest_jobs) <= _MAX_IN_MEMORY_BACKTEST_JOBS:
        return
    removable = [
        candidate
        for candidate in _backtest_jobs.values()
        if candidate.get("status") != "RUNNING" and candidate.get("job_id") != job.get("job_id")
    ]
    removable.sort(key=lambda candidate: str(candidate.get("finished_at") or candidate.get("started_at") or ""))
    while len(_backtest_jobs) > _MAX_IN_MEMORY_BACKTEST_JOBS and removable:
        oldest = removable.pop(0)
        _backtest_jobs.pop(str(oldest.get("job_id")), None)


async def _run_backtest_job(
    job_id: str,
    database_url: str,
    history: PostgresBtcBacktestHistoryStore,
) -> None:
    async with _backtest_lock:
        job = _backtest_jobs[job_id]

        def update(completed: int, total: int, phase: str) -> None:
            job.update(completed_clicks=completed, total_clicks=total, phase=phase)

        try:
            job["result"] = await run_first24h_underlying_15m(
                database_url,
                progress_callback=update,
            )
            job.update(
                status="COMPLETED",
                phase="COMPLETED",
                completed_clicks=job["total_clicks"],
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                await history.save_completed(job, job["result"])
                job["history_persisted"] = True
            except Exception as exc:
                job["history_persisted"] = False
                job["history_error"] = f"{exc.__class__.__name__}: {str(exc)[:300]}"
        except Exception as exc:
            job.update(
                status="FAILED",
                phase="FAILED",
                error=f"{exc.__class__.__name__}: {str(exc)[:300]}",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                await history.save_failed(job)
                job["history_persisted"] = True
            except Exception as persist_exc:
                job["history_persisted"] = False
                job["history_error"] = f"{persist_exc.__class__.__name__}: {str(persist_exc)[:300]}"
        finally:
            if not job.get("finished_at"):
                job["finished_at"] = datetime.now(timezone.utc).isoformat()


def register_crypto_btc_dashboard_action_routes(app, settings) -> None:
    @app.post("/v1/dashboard/crypto/btc/actions/first-24h-underlying-backtest")
    async def run_user_underlying_backtest(response: Response):
        if _backtest_lock.locked():
            raise HTTPException(status_code=409, detail="BTC underlying backtest is already running")
        database_url = _database_url(settings)
        response.headers["Cache-Control"] = "no-store"
        history = PostgresBtcBacktestHistoryStore(database_url)
        try:
            await history.initialize()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC backtest history is unavailable: {exc}") from exc

        job_id = _request_id()
        job = {
            "job_id": job_id,
            "status": "RUNNING",
            "phase": "QUEUED",
            "completed_clicks": 0,
            "total_clicks": 96,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "result": None,
            "history_persisted": False,
            "history_error": None,
        }
        try:
            await history.create_job(job)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC backtest could not be stored before start: {exc}") from exc
        _remember_in_memory(job)
        asyncio.create_task(_run_backtest_job(job_id, database_url, history))
        return _public_job(job)

    @app.get("/v1/dashboard/crypto/btc/actions/first-24h-underlying-backtest/{job_id}")
    async def read_user_underlying_backtest(job_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        job = _backtest_jobs.get(str(job_id))
        if job is not None:
            return _public_job(job)

        database_url = _database_url(settings)
        history = PostgresBtcBacktestHistoryStore(database_url)
        try:
            await history.initialize()
            persisted = await history.get_job(str(job_id))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC backtest history is unavailable: {exc}") from exc
        if persisted is None:
            raise HTTPException(status_code=404, detail="BTC backtest job is unavailable or expired")
        return persisted

    @app.get("/v1/dashboard/crypto/btc/actions/first-24h-underlying-backtests")
    async def list_user_underlying_backtests(
        response: Response,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        response.headers["Cache-Control"] = "no-store"
        database_url = _database_url(settings)
        history = PostgresBtcBacktestHistoryStore(database_url)
        try:
            await history.initialize()
            items = await history.list_jobs(limit=limit)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC backtest history is unavailable: {exc}") from exc
        return {
            "mode": "BTC_DASHBOARD_BACKTEST_HISTORY_V1",
            "status": "AVAILABLE",
            "count": len(items),
            "items": items,
        }

    @app.post("/v1/dashboard/crypto/btc/actions/live-shadow-click")
    async def generate_user_live_shadow_setup(response: Response):
        if _live_click_lock.locked():
            raise HTTPException(status_code=409, detail="A BTC live shadow setup is already being generated")
        database_url = _database_url(settings)
        response.headers["Cache-Control"] = "no-store"
        async with _live_click_lock:
            try:
                proof_config = BtcProspectiveProofRuntimeConfig.from_env()
                result = await run_explicit_live_shadow_click(
                    request_id=_request_id(),
                    database_url=database_url,
                    proof_config=proof_config,
                )
            except ValueError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "mode": "BTC_DASHBOARD_LIVE_SHADOW_SETUP_V1",
            "status": "GENERATED",
            "research_only": True,
            "order_placed": False,
            "live_execution": False,
            "capital_committed_inr": 0,
            "futures_trade_generated": False,
            "result": result,
        }


def architecture_contract() -> dict:
    return {
        "version": "BTC_DASHBOARD_USER_ACTIONS_CONTRACT_V2",
        "user_backtest_allowed": True,
        "user_backtest_reports_real_progress": True,
        "user_backtest_history_persisted": True,
        "user_backtest_history_readable": True,
        "user_live_shadow_setup_allowed": True,
        "broker_order_placement_allowed": False,
        "credentials_accepted_from_browser": False,
        "caller_supplied_decision_time_allowed": False,
        "caller_supplied_request_id_allowed": False,
        "futures_trade_generation_allowed": False,
        "live_execution": False,
        "capital_committed_inr": 0,
    }
