"""User-triggered BTC research actions for the Crypto dashboard.

These endpoints create research/shadow artifacts only. They never accept broker
credentials, place orders, invoke a Futures route, or commit capital.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import uuid4

from fastapi import HTTPException, Query, Response

from app.crypto_btc_backtest_history import PostgresBtcBacktestHistoryStore
from app.crypto_btc_continuous_direction_backtest import (
    continuous_direction_readiness,
    run_continuous_direction_15m,
)
from app.crypto_btc_enriched24h_underlying_backtest import (
    enriched_24h_readiness,
    run_enriched24h_underlying_15m,
)
from app.crypto_btc_first24h_underlying_backtest import run_first24h_underlying_15m
from app.crypto_btc_live_shadow_click import run_explicit_live_shadow_click
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig

_backtest_lock = asyncio.Lock()
_live_click_lock = asyncio.Lock()
_backtest_jobs: dict[str, dict] = {}
_MAX_IN_MEMORY_BACKTEST_JOBS = 20
_PROGRESS_CHECKPOINT_EVERY_CLICKS = 4
BacktestRunner = Callable[..., Awaitable[dict]]


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
        "mode": job.get("mode"),
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


def _has_active_in_memory_backtest() -> bool:
    return any(job.get("status") == "RUNNING" for job in _backtest_jobs.values())


async def _reconcile_stale_history(history: PostgresBtcBacktestHistoryStore) -> None:
    # The production service currently runs one worker instance. If this process
    # has no active job, a stale RUNNING row belongs to a worker that disappeared
    # during an OOM/restart/deploy and must not remain RUNNING forever.
    if _has_active_in_memory_backtest():
        return
    await history.fail_stale_running(stale_after_seconds=45)


async def _run_backtest_job(
    job_id: str,
    database_url: str,
    history: PostgresBtcBacktestHistoryStore,
    runner: BacktestRunner,
) -> None:
    async with _backtest_lock:
        job = _backtest_jobs[job_id]
        last_persisted_completed = -1
        last_persisted_phase: str | None = None

        async def update(completed: int, total: int, phase: str) -> None:
            nonlocal last_persisted_completed, last_persisted_phase
            job.update(completed_clicks=completed, total_clicks=total, phase=phase)
            should_checkpoint = (
                phase != last_persisted_phase
                or completed == total
                or completed == 0
                or completed - last_persisted_completed >= _PROGRESS_CHECKPOINT_EVERY_CLICKS
            )
            if not should_checkpoint:
                return
            try:
                await history.save_progress(job)
                last_persisted_completed = completed
                last_persisted_phase = phase
            except Exception as exc:
                # Progress durability must never abort the frozen research replay.
                job["history_error"] = f"Progress checkpoint {exc.__class__.__name__}: {str(exc)[:260]}"

        try:
            job["result"] = await runner(
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
                job["history_error"] = None
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


async def _create_backtest_job(
    *,
    database_url: str,
    history: PostgresBtcBacktestHistoryStore,
    mode: str,
    runner: BacktestRunner,
    total_clicks: int = 96,
) -> dict:
    job_id = _request_id()
    job = {
        "job_id": job_id,
        "mode": mode,
        "status": "RUNNING",
        "phase": "QUEUED",
        "completed_clicks": 0,
        "total_clicks": max(1, int(total_clicks)),
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
    asyncio.create_task(_run_backtest_job(job_id, database_url, history, runner))
    return _public_job(job)


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
            await _reconcile_stale_history(history)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC backtest history is unavailable: {exc}") from exc
        return await _create_backtest_job(
            database_url=database_url,
            history=history,
            mode="FIRST_SHARED_24H",
            runner=run_first24h_underlying_15m,
        )

    @app.get("/v1/dashboard/crypto/btc/actions/enriched-24h-underlying-backtest/readiness")
    async def read_enriched_underlying_readiness(response: Response):
        response.headers["Cache-Control"] = "no-store"
        database_url = _database_url(settings)
        try:
            return await enriched_24h_readiness(database_url)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC enriched replay readiness is unavailable: {exc}") from exc

    @app.post("/v1/dashboard/crypto/btc/actions/enriched-24h-underlying-backtest")
    async def run_user_enriched_underlying_backtest(response: Response):
        if _backtest_lock.locked():
            raise HTTPException(status_code=409, detail="BTC underlying backtest is already running")
        database_url = _database_url(settings)
        response.headers["Cache-Control"] = "no-store"
        try:
            readiness = await enriched_24h_readiness(database_url)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC enriched replay readiness is unavailable: {exc}") from exc
        if readiness.get("ready") is not True:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "BTC_ENRICHED_24H_NOT_READY",
                    "message": str(readiness.get("next_requirement") or "Enriched BTC 24h replay is still collecting context."),
                    "readiness": readiness,
                },
            )

        history = PostgresBtcBacktestHistoryStore(database_url)
        try:
            await history.initialize()
            await _reconcile_stale_history(history)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC backtest history is unavailable: {exc}") from exc
        return await _create_backtest_job(
            database_url=database_url,
            history=history,
            mode="ENRICHED_PIT_24H",
            runner=run_enriched24h_underlying_15m,
        )

    @app.get("/v1/dashboard/crypto/btc/actions/continuous-direction-backtest/readiness")
    async def read_continuous_direction_readiness(response: Response):
        response.headers["Cache-Control"] = "no-store"
        database_url = _database_url(settings)
        try:
            return await continuous_direction_readiness(database_url)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC continuous direction readiness is unavailable: {exc}") from exc

    @app.post("/v1/dashboard/crypto/btc/actions/continuous-direction-backtest")
    async def run_user_continuous_direction_backtest(response: Response):
        if _backtest_lock.locked():
            raise HTTPException(status_code=409, detail="BTC underlying backtest is already running")
        database_url = _database_url(settings)
        response.headers["Cache-Control"] = "no-store"
        try:
            readiness = await continuous_direction_readiness(database_url)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC continuous direction readiness is unavailable: {exc}") from exc
        if readiness.get("ready") is not True:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "BTC_CONTINUOUS_DIRECTION_NOT_READY",
                    "message": str(readiness.get("reason") or "BTC PIT history is not ready for a 15-minute direction grid."),
                    "readiness": readiness,
                },
            )

        history = PostgresBtcBacktestHistoryStore(database_url)
        try:
            await history.initialize()
            await _reconcile_stale_history(history)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"BTC backtest history is unavailable: {exc}") from exc
        return await _create_backtest_job(
            database_url=database_url,
            history=history,
            mode="CONTINUOUS_PIT_15M_DIRECTION",
            runner=run_continuous_direction_15m,
            total_clicks=int(readiness.get("scheduled_clicks") or 1),
        )

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
            await _reconcile_stale_history(history)
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
            await _reconcile_stale_history(history)
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
        "version": "BTC_DASHBOARD_USER_ACTIONS_CONTRACT_V5",
        "user_backtest_allowed": True,
        "user_backtest_reports_real_progress": True,
        "user_backtest_progress_checkpointed": True,
        "interrupted_backtests_reconciled": True,
        "user_backtest_history_persisted": True,
        "user_backtest_history_readable": True,
        "original_first_24h_replay_preserved": True,
        "later_enriched_24h_replay_available": True,
        "later_enriched_24h_requires_readiness_gate": True,
        "enriched_replay_may_run_before_full_context_window": False,
        "continuous_direction_replay_available": True,
        "continuous_direction_replay_uses_dynamic_click_count": True,
        "continuous_direction_replay_options_profitability": False,
        "production_two_origin_gate_changed": False,
        "user_live_shadow_setup_allowed": True,
        "broker_order_placement_allowed": False,
        "credentials_accepted_from_browser": False,
        "caller_supplied_decision_time_allowed": False,
        "caller_supplied_request_id_allowed": False,
        "futures_trade_generation_allowed": False,
        "live_execution": False,
        "capital_committed_inr": 0,
    }
