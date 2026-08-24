from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from typing import Literal
import logging
import traceback

from .backtest import run_backtest
from .commodity_backtest import run_commodity_backtest
from .commodity_continuous_backtest import run_continuous_commodity_backtest
from .commodities import commodity_candles, commodity_probe, commodity_quote, resolve_nearest_mcx_future
from .commodity_scanner import commodity_mtf_scan
from .external_context import external_market_context
from .news import latest_commodity_news, latest_market_news
from .providers.factory import get_provider

logger = logging.getLogger("alphapilot.scan")


class Settings(BaseSettings):
    market_data_provider: str = "MOCK"
    allowed_origins: str = "*"

    class Config:
        env_file = ".env"


settings = Settings()
app = FastAPI(title="AlphaPilot API", version="0.15.0")
parsed_origins = [x.strip() for x in settings.allowed_origins.split(",") if x.strip()]
if "*" in parsed_origins: parsed_origins = ["*"]
app.add_middleware(CORSMiddleware, allow_origins=parsed_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
TF = Literal["5m","15m","1h","1d"]


def _safe_upstream_error(operation: str, exc: Exception):
    """Return browser-readable Groww diagnostics without leaking credentials."""
    response = getattr(exc, "response", None)
    request = getattr(exc, "request", None) or getattr(response, "request", None)
    status = getattr(response, "status_code", None)
    text = str(exc)

    upstream_path = None
    try:
        upstream_path = getattr(getattr(request, "url", None), "path", None)
    except Exception:
        upstream_path = None

    if upstream_path and "/v1/token/api/access" in upstream_path:
        source = "AUTH"
    elif upstream_path and "/live-data/" in upstream_path:
        source = "LIVE_DATA"
    elif upstream_path and "/historical/" in upstream_path:
        source = "HISTORICAL"
    elif upstream_path and "/option-chain/" in upstream_path:
        source = "OPTION_CHAIN"
    else:
        source = "UPSTREAM"

    if status:
        detail = f"Groww {operation} failed: {source} HTTP {status}"
        if upstream_path:
            detail += f" at {upstream_path}"
    elif "timeout" in text.lower():
        detail = f"Groww {operation} timed out ({source})"
    else:
        detail = f"Groww {operation} failed: {exc.__class__.__name__} ({source})"

    logger.error(
        "Groww %s error source=%s status=%s path=%s error=%r",
        operation, source, status, upstream_path, exc,
    )

    # Preserve rate-limit semantics so the frontend knows not to retry a 429.
    public_status = 429 if status == 429 else 502
    raise HTTPException(status_code=public_status, detail=detail)


class ScanRequest(BaseModel):
    symbols:list[str]=Field(default_factory=lambda:["RELIANCE"])
    timeframe:TF="15m"
    min_risk_reward:float=1.5

class MTFRequest(BaseModel):
    symbols:list[str]=Field(default_factory=lambda:["RELIANCE"])
    timeframes:list[TF]=Field(default_factory=lambda:["5m","15m","1h"])
    min_risk_reward:float=1.5

class ManualGift(BaseModel):
    ltp: float
    change_pct: float
    entered_at: str | None = None

class FNORequest(BaseModel):
    symbol:str="RELIANCE"
    timeframes:list[TF]=Field(default_factory=lambda:["5m","15m","1h"])
    min_risk_reward:float=1.5
    expiry:str|None=None
    include_market:bool=True
    take_snapshot:bool=True
    manual_gift:ManualGift|None=None

class SnapshotRequest(BaseModel):
    symbol:str="RELIANCE"
    expiry:str|None=None

class BacktestRequest(BaseModel):
    symbols:list[str]=Field(default_factory=lambda:["RELIANCE"])
    start_date:str
    end_date:str
    min_risk_reward:float=1.5
    entry_before:str|None=None

class CommodityBacktestRequest(BaseModel):
    symbol:Literal["CRUDEOIL","NATURALGAS"]
    days:int=30
    min_risk_reward:float=1.5
    strength_threshold:float=65.0
    slippage_bps:float=2.0
    cost_bps:float=2.0

@app.get("/")
async def root(): return {"ok":True,"service":"alphapilot-api"}

@app.get("/health")
async def health(): return {"ok":True,"service":"alphapilot-api","version":"0.15.0","provider":settings.market_data_provider.upper()}

@app.get("/v1/quote/{symbol}")
async def quote(symbol:str):
    try:
        return await get_provider(settings).quote(symbol.upper())
    except Exception as exc:
        _safe_upstream_error("quote", exc)

@app.get("/v1/candles/{symbol}")
async def candles(symbol:str,timeframe:TF="15m"):
    try:
        data=await get_provider(settings).candles(symbol.upper(),timeframe)
        return {"symbol":symbol.upper(),"timeframe":timeframe,"candles":data}
    except Exception as exc:
        _safe_upstream_error("candles", exc)

@app.get("/v1/options/{symbol}")
async def options(symbol:str,expiry:str|None=None):
    try:
        return await get_provider(settings).option_chain(symbol.upper(),expiry)
    except Exception as exc:
        _safe_upstream_error("option chain", exc)

@app.get("/v1/commodity/contract/{symbol}")
async def commodity_contract(symbol:str):
    return await resolve_nearest_mcx_future(symbol.upper())

@app.get("/v1/commodity/quote/{symbol}")
async def mcx_quote(symbol:str):
    return await commodity_quote(get_provider(settings),symbol.upper())

@app.get("/v1/commodity/candles/{symbol}")
async def mcx_candles(symbol:str,timeframe:Literal["5m","15m","1h"]="5m"):
    return await commodity_candles(get_provider(settings),symbol.upper(),timeframe)

@app.get("/v1/commodity/probe/{symbol}")
async def mcx_probe(symbol:str):
    return await commodity_probe(get_provider(settings),symbol.upper())

@app.get("/v1/commodity/scan/{symbol}")
async def mcx_scan(symbol:str,min_risk_reward:float=1.5):
    return await commodity_mtf_scan(get_provider(settings),symbol.upper(),min_risk_reward)

@app.get("/v1/commodity/news/{symbol}")
async def mcx_news(symbol:str,limit:int=4):
    return await latest_commodity_news(symbol.upper(),max(1,min(int(limit),6)))

@app.post("/v1/commodity/backtest")
async def mcx_backtest(request:CommodityBacktestRequest):
    return await run_commodity_backtest(
        get_provider(settings), request.symbol, request.days, request.min_risk_reward,
        request.strength_threshold, request.slippage_bps, request.cost_bps,
    )

@app.post("/v1/commodity/backtest/continuous")
async def mcx_continuous_backtest(request:CommodityBacktestRequest):
    return await run_continuous_commodity_backtest(
        get_provider(settings), request.symbol, request.days, request.min_risk_reward,
        request.strength_threshold, request.slippage_bps, request.cost_bps,
    )

@app.get("/v1/news")
async def news(symbols:str,limit:int=3):
    parsed=[x.strip().upper() for x in symbols.split(",") if x.strip()]
    safe_limit=max(1,min(int(limit),5))
    return await latest_market_news(parsed,safe_limit)

@app.post("/v1/scan")
async def scan(request:ScanRequest): return await get_provider(settings).scan(request.symbols,request.timeframe,request.min_risk_reward)

@app.post("/v1/scan/mtf")
async def mtf(request:MTFRequest):
    symbols=[s.upper() for s in request.symbols]
    try:
        result=await get_provider(settings).multi_timeframe_scan(symbols,request.timeframes,request.min_risk_reward)
        if isinstance(result,list):
            for item in result:
                if isinstance(item,dict) and (item.get("error") or item.get("status") in {"ERROR","FAILED"}):
                    logger.error("MTF symbol failure symbol=%s status=%s error=%s",item.get("symbol"),item.get("status"),item.get("error"))
        elif isinstance(result,dict):
            rows=result.get("results") or result.get("items") or []
            if isinstance(rows,list):
                for item in rows:
                    if isinstance(item,dict) and (item.get("error") or item.get("status") in {"ERROR","FAILED"}):
                        logger.error("MTF symbol failure symbol=%s status=%s error=%s",item.get("symbol"),item.get("status"),item.get("error"))
        return result
    except Exception as exc:
        logger.error("MTF request crashed symbols=%s timeframes=%s error=%s\n%s",symbols,request.timeframes,repr(exc),traceback.format_exc())
        raise

@app.post("/v1/backtest")
async def backtest(request:BacktestRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()]
    if not symbols:
        symbols=["RELIANCE"]
    return await run_backtest(get_provider(settings),symbols,request.start_date,request.end_date,request.min_risk_reward,request.entry_before)

@app.get("/v1/market/context")
async def market_context(timeframes:str="5m,15m,1h"):
    parsed=[x.strip() for x in timeframes.split(",") if x.strip() in {"5m","15m","1h","1d"}] or ["5m","15m","1h"]
    return await get_provider(settings).market_context(parsed)

@app.get("/v1/context/external/{symbol}")
async def external_context(symbol:str): return await external_market_context(symbol.upper())

@app.post("/v1/fno/snapshot")
async def fno_snapshot(request:SnapshotRequest): return await get_provider(settings).take_option_snapshot(request.symbol.upper(),request.expiry)

@app.post("/v1/scan/fno")
async def fno(request:FNORequest):
    manual = request.manual_gift.model_dump() if request.manual_gift else None
    return await get_provider(settings).fno_confirm(request.symbol.upper(),request.timeframes,request.min_risk_reward,request.expiry,request.include_market,request.take_snapshot,manual)
