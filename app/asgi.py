from fastapi import HTTPException
from pydantic import BaseModel, Field

from .candidate_validator import run_candidate_validator
from .candidate_h_option_validator import run_candidate_h_option_validator
from .candlestick_research import run_candlestick_research
from .main import app, settings, _safe_upstream_error
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
