from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from typing import Literal
import logging
import traceback

from .backtest import run_backtest
from .external_context import external_market_context
from .news import latest_market_news
from .providers.factory import get_provider

logger = logging.getLogger("alphapilot.scan")


class Settings(BaseSettings):
    market_data_provider: str = "MOCK"
    allowed_origins: str = "*"

    class Config:
        env_file = ".env"


settings = Settings()
app = FastAPI(title="AlphaPilot API", version="0.11.0")
parsed_origins = [x.strip() for x in settings.allowed_origins.split(",") if x.strip()]
if "*" in parsed_origins: parsed_origins = ["*"]
app.add_middleware(CORSMiddleware, allow_origins=parsed_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
TF = Literal["5m","15m","1h","1d"]

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

@app.get("/")
async def root(): return {"ok":True,"service":"alphapilot-api"}

@app.get("/health")
async def health(): return {"ok":True,"service":"alphapilot-api","version":"0.11.0","provider":settings.market_data_provider.upper()}

@app.get("/v1/quote/{symbol}")
async def quote(symbol:str): return await get_provider(settings).quote(symbol.upper())

@app.get("/v1/candles/{symbol}")
async def candles(symbol:str,timeframe:TF="15m"):
    data=await get_provider(settings).candles(symbol.upper(),timeframe)
    return {"symbol":symbol.upper(),"timeframe":timeframe,"candles":data}

@app.get("/v1/options/{symbol}")
async def options(symbol:str,expiry:str|None=None): return await get_provider(settings).option_chain(symbol.upper(),expiry)

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
    return await run_backtest(
        get_provider(settings),
        symbols,
        request.start_date,
        request.end_date,
        request.min_risk_reward,
        request.entry_before,
    )

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
