from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from typing import Literal
from .providers.factory import get_provider


class Settings(BaseSettings):
    market_data_provider: str = "MOCK"
    allowed_origins: str = "http://localhost:5173"

    class Config:
        env_file = ".env"


settings = Settings()
app = FastAPI(title="AlphaPilot API", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in settings.allowed_origins.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScanRequest(BaseModel):
    symbols: list[str] = Field(
        default_factory=lambda: [
            "NIFTY",
            "BANKNIFTY",
            "RELIANCE",
            "TCS",
            "INFY",
            "HDFCBANK",
            "ICICIBANK",
            "SBIN",
        ]
    )
    timeframe: Literal["5m", "15m", "1h", "1d"] = "15m"
    min_risk_reward: float = 1.5


@app.get("/")
async def root():
    return {
        "ok": True,
        "service": "alphapilot-api",
        "message": "AlphaPilot API is running",
    }


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "alphapilot-api",
        "version": "0.4.0",
        "provider": settings.market_data_provider.upper(),
    }


@app.get("/v1/quote/{symbol}")
async def quote(symbol: str):
    provider = get_provider(settings)
    return await provider.quote(symbol.upper())


@app.get("/v1/candles/{symbol}")
async def candles(
    symbol: str,
    timeframe: Literal["5m", "15m", "1h", "1d"] = "15m",
):
    provider = get_provider(settings)
    data = await provider.candles(symbol.upper(), timeframe)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "candles": data,
    }


@app.get("/v1/options/{symbol}")
async def option_chain(symbol: str, expiry: str | None = None):
    provider = get_provider(settings)
    return await provider.option_chain(symbol.upper(), expiry)


@app.post("/v1/scan")
async def scan(request: ScanRequest):
    provider = get_provider(settings)
    return await provider.scan(
        request.symbols,
        request.timeframe,
        request.min_risk_reward,
    )
