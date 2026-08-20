from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field
from pydantic_settings import BaseSettings
from typing import Literal
from .providers.factory import get_provider

class Settings(BaseSettings):
    market_data_provider:str="MOCK"; allowed_origins:str="http://localhost:5173"
    class Config:env_file=".env"
settings=Settings()
app=FastAPI(title="AlphaPilot API",version="0.7.0")
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in settings.allowed_origins.split(",")],
                   allow_credentials=False,allow_methods=["*"],allow_headers=["*"])

TF=Literal["5m","15m","1h","1d"]
class ScanRequest(BaseModel):
    symbols:list[str]=Field(default_factory=lambda:["RELIANCE"]); timeframe:TF="15m"; min_risk_reward:float=1.5
class MTFRequest(BaseModel):
    symbols:list[str]=Field(default_factory=lambda:["RELIANCE"])
    timeframes:list[TF]=Field(default_factory=lambda:["5m","15m","1h"]); min_risk_reward:float=1.5
class FNORequest(BaseModel):
    symbol:str="RELIANCE"; timeframes:list[TF]=Field(default_factory=lambda:["5m","15m","1h"])
    min_risk_reward:float=1.5; expiry:str|None=None

@app.get("/")
async def root():return {"ok":True,"service":"alphapilot-api"}
@app.get("/health")
async def health():return {"ok":True,"service":"alphapilot-api","version":"0.7.0","provider":settings.market_data_provider.upper()}
@app.get("/v1/quote/{symbol}")
async def quote(symbol:str):return await get_provider(settings).quote(symbol.upper())
@app.get("/v1/candles/{symbol}")
async def candles(symbol:str,timeframe:TF="15m"):
    return {"symbol":symbol.upper(),"timeframe":timeframe,"candles":await get_provider(settings).candles(symbol.upper(),timeframe)}
@app.get("/v1/options/{symbol}")
async def options(symbol:str,expiry:str|None=None):return await get_provider(settings).option_chain(symbol.upper(),expiry)
@app.post("/v1/scan")
async def scan(r:ScanRequest):return await get_provider(settings).scan(r.symbols,r.timeframe,r.min_risk_reward)
@app.post("/v1/scan/mtf")
async def mtf(r:MTFRequest):return await get_provider(settings).multi_timeframe_scan(r.symbols,r.timeframes,r.min_risk_reward)
@app.post("/v1/scan/fno")
async def fno(r:FNORequest):return await get_provider(settings).fno_confirm(r.symbol.upper(),r.timeframes,r.min_risk_reward,r.expiry)
