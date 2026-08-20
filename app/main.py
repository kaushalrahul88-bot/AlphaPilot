from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from typing import Literal

from .providers.factory import get_provider


class Settings(BaseSettings):
    market_data_provider: str = "MOCK"
    # Comma-separated origins. Use * for public browser access.
    allowed_origins: str = "*"

    class Config:
        env_file = ".env"


settings = Settings()

app = FastAPI(
    title="AlphaPilot API",
    version="0.8.0",
)

parsed_origins = [
    x.strip()
    for x in settings.allowed_origins.split(",")
    if x.strip()
]

# The frontend does not send cookies/HTTP auth credentials, so wildcard CORS
# is safe for this public research API and works with Bolt preview origins.
if "*" in parsed_origins:
    parsed_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=parsed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


TF = Literal[
    "5m",
    "15m",
    "1h",
    "1d",
]


class ScanRequest(BaseModel):
    symbols: list[str] = Field(
        default_factory=lambda: ["RELIANCE"]
    )
    timeframe: TF = "15m"
    min_risk_reward: float = 1.5


class MTFRequest(BaseModel):
    symbols: list[str] = Field(
        default_factory=lambda: ["RELIANCE"]
    )
    timeframes: list[TF] = Field(
        default_factory=lambda: [
            "5m",
            "15m",
            "1h",
        ]
    )
    min_risk_reward: float = 1.5


class FNORequest(BaseModel):
    symbol: str = "RELIANCE"
    timeframes: list[TF] = Field(
        default_factory=lambda: [
            "5m",
            "15m",
            "1h",
        ]
    )
    min_risk_reward: float = 1.5
    expiry: str | None = None
    include_market: bool = True
    take_snapshot: bool = True


class SnapshotRequest(BaseModel):
    symbol: str = "RELIANCE"
    expiry: str | None = None


@app.get("/")
async def root():
    return {
        "ok": True,
        "service": "alphapilot-api",
    }


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "alphapilot-api",
        "version": "0.8.0",
        "provider": settings.market_data_provider.upper(),
    }


@app.get("/v1/quote/{symbol}")
async def quote(symbol: str):
    return await get_provider(
        settings
    ).quote(
        symbol.upper()
    )


@app.get("/v1/candles/{symbol}")
async def candles(
    symbol: str,
    timeframe: TF = "15m",
):
    data = await get_provider(
        settings
    ).candles(
        symbol.upper(),
        timeframe,
    )

    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "candles": data,
    }


@app.get("/v1/options/{symbol}")
async def options(
    symbol: str,
    expiry: str | None = None,
):
    return await get_provider(
        settings
    ).option_chain(
        symbol.upper(),
        expiry,
    )


@app.post("/v1/scan")
async def scan(request: ScanRequest):
    return await get_provider(
        settings
    ).scan(
        request.symbols,
        request.timeframe,
        request.min_risk_reward,
    )


@app.post("/v1/scan/mtf")
async def mtf(request: MTFRequest):
    return await get_provider(
        settings
    ).multi_timeframe_scan(
        request.symbols,
        request.timeframes,
        request.min_risk_reward,
    )


@app.get("/v1/market/context")
async def market_context(
    timeframes: str = "5m,15m,1h",
):
    parsed = [
        x.strip()
        for x in timeframes.split(",")
        if x.strip() in {
            "5m",
            "15m",
            "1h",
            "1d",
        }
    ]

    if not parsed:
        parsed = [
            "5m",
            "15m",
            "1h",
        ]

    return await get_provider(
        settings
    ).market_context(
        parsed
    )


@app.post("/v1/fno/snapshot")
async def fno_snapshot(
    request: SnapshotRequest,
):
    return await get_provider(
        settings
    ).take_option_snapshot(
        request.symbol.upper(),
        request.expiry,
    )


@app.post("/v1/scan/fno")
async def fno(
    request: FNORequest,
):
    return await get_provider(
        settings
    ).fno_confirm(
        request.symbol.upper(),
        request.timeframes,
        request.min_risk_reward,
        request.expiry,
        request.include_market,
        request.take_snapshot,
    )
