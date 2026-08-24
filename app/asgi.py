from fastapi import HTTPException
from pydantic import BaseModel, Field

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


@app.post("/v1/research/market-regime")
async def market_regime_research(request: MarketRegimeResearchRequest):
    symbols = [s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:
        return await run_market_regime_research(
            get_provider(settings),
            symbols,
            request.start_date,
            request.end_date,
            request.premium_min_risk_reward,
            request.max_trades_per_model,
            request.round_trip_cost_bps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _safe_upstream_error("market regime research", exc)
