import asyncio
import traceback

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from .candidate_validator import run_candidate_validator
from .candidate_h_option_validator import run_candidate_h_option_validator
from .candlestick_research import run_candlestick_research
from .commodity_candle_collector import PostgresCandleStore
from .crude_oil_mini_live_inputs import collect_live_crude_news, read_live_crude_inputs
from .crude_oil_mini_research_framework import framework_summary, run_crude_oil_mini_research_framework
from .current_mind_copper_forward import run_forward_phase1_from_provider
from .main import app, settings, _safe_upstream_error, _collector_store
from .market_regime_research import run_market_regime_research
from .providers.factory import get_provider


class MarketRegimeResearchRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: [
        "RELIANCE", "SBIN", "AXISBANK", "HDFCBANK", "ICICIBANK",
        "TATASTEEL", "HINDALCO", "ONGC", "INFY", "TCS",
    ])
    start_date: str
    end_date: str
    premium_min_risk_reward: float = 1.5
    max_trades_per_model: int = 30
    round_trip_cost_bps: float = 10.0


class CandidateValidatorRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: [
        "MARUTI", "EICHERMOT", "INDUSINDBK", "JSWSTEEL", "TITAN",
        "NESTLEIND", "GRASIM", "BRITANNIA", "LT", "DRREDDY",
        "BAJFINANCE", "M&M", "SUNPHARMA", "ADANIPORTS", "KOTAKBANK",
    ])
    start_date: str
    end_date: str
    round_trip_cost_bps: float = 10.0
    sample_every_bars: int = 3
    max_trades: int = 250


class CandlestickResearchRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: [
        "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK",
        "TCS", "INFY", "HCLTECH", "TATASTEEL", "JSWSTEEL",
        "HINDALCO", "MARUTI", "M&M", "SUNPHARMA", "DRREDDY",
        "CIPLA", "ITC", "TITAN", "LT", "ADANIPORTS",
    ])
    start_date: str
    end_date: str


class CandidateHOptionRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: [
        "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "INFY",
        "TATASTEEL", "MARUTI", "AXISBANK", "KOTAKBANK", "LT", "HINDALCO",
    ])
    start_date: str
    end_date: str
    max_signals: int = 80


@app.post("/v1/research/market-regime")
async def market_regime_research(request: MarketRegimeResearchRequest):
    symbols = [s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:
        return await run_market_regime_research(
            get_provider(settings), symbols, request.start_date, request.end_date,
            request.premium_min_risk_reward, request.max_trades_per_model,
            request.round_trip_cost_bps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _safe_upstream_error("market regime research", exc)


@app.post("/v1/research/candidate-validator")
async def candidate_validator(request: CandidateValidatorRequest):
    symbols = [s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:
        return await run_candidate_validator(
            get_provider(settings), symbols, request.start_date, request.end_date,
            request.round_trip_cost_bps, request.sample_every_bars, request.max_trades,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _safe_upstream_error("candidate validator", exc)


@app.post("/v1/research/candlestick-discovery")
async def candlestick_discovery(request: CandlestickResearchRequest):
    symbols = [s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:
        return await run_candlestick_research(
            get_provider(settings), symbols, request.start_date, request.end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _safe_upstream_error("candlestick discovery", exc)


@app.post("/v1/research/candidate-h-option-oos")
async def candidate_h_option_oos(request: CandidateHOptionRequest):
    symbols = [s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:
        return await run_candidate_h_option_validator(
            get_provider(settings), symbols, request.start_date, request.end_date, request.max_signals,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _safe_upstream_error("Candidate H option OOS", exc)


_copper_forward_phase1_job = {"status": "IDLE", "result": None, "error": None}
_copper_forward_phase1_task = None


def _run_copper_forward_phase1_sync():
    global _copper_forward_phase1_job
    try:
        result = asyncio.run(run_forward_phase1_from_provider(get_provider(settings)))
        _copper_forward_phase1_job = {"status": "COMPLETED", "result": result, "error": None}
    except Exception as exc:
        _copper_forward_phase1_job = {
            "status": "FAILED",
            "result": None,
            "error": str(exc)[:1000],
            "traceback": traceback.format_exc()[-4000:],
        }


def _forward_phase1_operational_view(job: dict) -> dict:
    status = str(job.get("status") or "UNKNOWN")
    if status != "COMPLETED":
        return {
            "status": status,
            "error": job.get("error") if status == "FAILED" else None,
            "score_revealed": False,
        }
    result = job.get("result") or {}
    return {
        "status": "COMPLETED",
        "mode": result.get("mode"),
        "research_only": result.get("research_only"),
        "production_rules_changed": result.get("production_rules_changed"),
        "live_execution_enabled": result.get("live_execution_enabled"),
        "as_of": result.get("as_of"),
        "reference_contract": result.get("reference_contract"),
        "contract_metadata": result.get("contract_metadata"),
        "bar_timing": result.get("bar_timing"),
        "eligible_sessions": result.get("eligible_sessions"),
        "excluded_sessions": result.get("excluded_sessions"),
        "phase1_complete": result.get("phase1_complete"),
        "scheduled_clicks": result.get("scheduled_clicks"),
        "evaluated_clicks": result.get("evaluated_clicks"),
        "click_coverage_exact": result.get("click_coverage_exact"),
        "validation_status": result.get("validation_status"),
        "score_revealed": False,
        "sealed_fields": [
            "actions", "trades", "resolved_trades", "targets", "stops",
            "no_entry", "session_end", "expectancy_r_resolved", "scorecard", "decisions",
        ],
    }


@app.post("/v1/internal/copper/current-mind-forward-phase1/start")
async def copper_current_mind_forward_phase1_start(
    x_collector_token: str | None = Header(default=None),
):
    global _copper_forward_phase1_job, _copper_forward_phase1_task
    _collector_store(x_collector_token)
    if _copper_forward_phase1_job.get("status") == "RUNNING":
        return _forward_phase1_operational_view(_copper_forward_phase1_job)
    _copper_forward_phase1_job = {"status": "RUNNING", "result": None, "error": None}
    _copper_forward_phase1_task = asyncio.create_task(asyncio.to_thread(_run_copper_forward_phase1_sync))
    return _forward_phase1_operational_view(_copper_forward_phase1_job)


@app.get("/v1/internal/copper/current-mind-forward-phase1/status")
async def copper_current_mind_forward_phase1_status(
    x_collector_token: str | None = Header(default=None),
):
    _collector_store(x_collector_token)
    return _forward_phase1_operational_view(_copper_forward_phase1_job)


@app.get("/v1/internal/copper/current-mind-forward-phase1/result")
async def copper_current_mind_forward_phase1_result(
    x_collector_token: str | None = Header(default=None),
):
    _collector_store(x_collector_token)
    if _copper_forward_phase1_job.get("status") != "COMPLETED":
        raise HTTPException(status_code=409, detail="Forward Phase 1 has not completed a runner pass")
    result = _copper_forward_phase1_job.get("result") or {}
    if not result.get("phase1_complete"):
        raise HTTPException(
            status_code=423,
            detail="Forward Phase 1 score is sealed until the preregistered Sep 2-11 window is complete",
        )
    return result


_crude_oil_mini_research_job = {"status": "IDLE", "result": None, "error": None}
_crude_oil_mini_research_task = None


async def _run_crude_oil_mini_research_job():
    global _crude_oil_mini_research_job
    try:
        store = PostgresCandleStore(settings.database_url)
        result = await run_crude_oil_mini_research_framework(
            get_provider(settings),
            store,
        )
        _crude_oil_mini_research_job = {
            "status": "COMPLETED",
            "result": result,
            "error": None,
        }
    except Exception as exc:
        _crude_oil_mini_research_job = {
            "status": "FAILED",
            "result": None,
            "error": f"{exc.__class__.__name__}: {str(exc)[:1200]}",
            "traceback": traceback.format_exc()[-6000:],
        }


def _crude_oil_mini_research_operational_view(job: dict) -> dict:
    status = str(job.get("status") or "UNKNOWN")
    if status != "COMPLETED":
        return {
            "status": status,
            "error": job.get("error") if status == "FAILED" else None,
            "research_only": True,
            "live_execution_enabled": False,
        }
    result = job.get("result") or {}
    return {
        "status": "COMPLETED",
        "research_only": True,
        "live_execution_enabled": False,
        "summary": framework_summary(result),
    }


@app.post("/v1/internal/crude-oil-mini/research-framework/start")
async def crude_oil_mini_research_framework_start(
    x_collector_token: str | None = Header(default=None),
):
    global _crude_oil_mini_research_job, _crude_oil_mini_research_task
    _collector_store(x_collector_token)
    if _crude_oil_mini_research_job.get("status") == "RUNNING":
        return _crude_oil_mini_research_operational_view(_crude_oil_mini_research_job)
    _crude_oil_mini_research_job = {"status": "RUNNING", "result": None, "error": None}
    _crude_oil_mini_research_task = asyncio.create_task(_run_crude_oil_mini_research_job())
    return _crude_oil_mini_research_operational_view(_crude_oil_mini_research_job)


@app.get("/v1/internal/crude-oil-mini/research-framework/status")
async def crude_oil_mini_research_framework_status(
    x_collector_token: str | None = Header(default=None),
):
    _collector_store(x_collector_token)
    return _crude_oil_mini_research_operational_view(_crude_oil_mini_research_job)


@app.get("/v1/internal/crude-oil-mini/research-framework/result")
async def crude_oil_mini_research_framework_result(
    x_collector_token: str | None = Header(default=None),
):
    _collector_store(x_collector_token)
    if _crude_oil_mini_research_job.get("status") != "COMPLETED":
        raise HTTPException(status_code=409, detail="Crude Oil Mini research framework job has not completed")
    return _crude_oil_mini_research_job.get("result") or {}


@app.post("/v1/internal/crude-oil-mini/news-collect")
async def crude_oil_mini_news_collect(
    x_collector_token: str | None = Header(default=None),
):
    _collector_store(x_collector_token)
    try:
        return await collect_live_crude_news(settings.database_url)
    except Exception as exc:
        _safe_upstream_error("Crude Oil Mini live news collection", exc)


@app.get("/v1/internal/crude-oil-mini/live-inputs")
async def crude_oil_mini_live_inputs(
    as_of: str,
    x_collector_token: str | None = Header(default=None),
):
    _collector_store(x_collector_token)
    try:
        return await read_live_crude_inputs(settings.database_url, click_at=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _safe_upstream_error("Crude Oil Mini PIT live inputs", exc)
