from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from typing import Literal
import logging
import traceback

from .backtest import run_backtest
from .candidate_validator import run_candidate_validator
from .candidate_b_validator import run_candidate_b_validator
from .candidate_h_option_validator import run_candidate_h_option_validator
from .candlestick_research import run_candlestick_research
from .candlestick_research_v2 import run_candlestick_research_v2
from .commodity_backtest import run_commodity_backtest
from .commodity_continuous_backtest import run_continuous_commodity_backtest
from .commodities import commodity_candles, commodity_probe, commodity_quote, resolve_nearest_mcx_future
from .commodity_scanner import commodity_mtf_scan
from .edge_discovery import run_edge_discovery
from .external_context import external_market_context
from .fno_history_probe import probe_historical_option_candles
from .fno_historical_backtest import run_true_premium_backtest
from .fno_premium_replay import replay_option_trade
from .global_intelligence import global_intelligence
from .market_brain_context_research import run_market_brain_context_block
from .market_brain_setup_expectancy import run_market_brain_setup_expectancy
from .market_brain_v7_regime_quality import evaluate_market_brain_v7, run_market_brain_v7_observations
from .market_regime_research import run_market_regime_research
from .news import latest_commodity_news, latest_market_news
from .option_native_research import run_option_native_research
from .option_native_phase2 import run_option_native_phase2
from .providers.factory import get_provider
from .setup_discovery_v2 import run_setup_discovery_v2
from .strategy_research import run_strategy_research
from .strategy_premium_replay import run_strategy_premium_replay

logger = logging.getLogger("alphapilot.scan")
class Settings(BaseSettings):
    market_data_provider: str = "MOCK"
    allowed_origins: str = "*"
    class Config: env_file = ".env"
settings=Settings(); app=FastAPI(title="AlphaPilot API",version="0.28.3"); parsed_origins=[x.strip() for x in settings.allowed_origins.split(",") if x.strip()]; parsed_origins=["*"] if "*" in parsed_origins else parsed_origins; app.add_middleware(CORSMiddleware,allow_origins=parsed_origins,allow_credentials=False,allow_methods=["*"],allow_headers=["*"]); TF=Literal["5m","15m","1h","1d"]

def _safe_upstream_error(operation:str,exc:Exception):
    response=getattr(exc,"response",None); request=getattr(exc,"request",None) or getattr(response,"request",None); status=getattr(response,"status_code",None); text=str(exc); upstream_path=None
    try: upstream_path=getattr(getattr(request,"url",None),"path",None)
    except Exception: pass
    if upstream_path and "/v1/token/api/access" in upstream_path: source="AUTH"
    elif upstream_path and "/live-data/" in upstream_path: source="LIVE_DATA"
    elif upstream_path and "/historical/" in upstream_path: source="HISTORICAL"
    elif upstream_path and "/option-chain/" in upstream_path: source="OPTION_CHAIN"
    else: source="UPSTREAM"
    if status: detail=f"Groww {operation} failed: {source} HTTP {status}"+(f" at {upstream_path}" if upstream_path else "")
    elif "timeout" in text.lower(): detail=f"Groww {operation} timed out ({source})"
    else: detail=f"Groww {operation} failed: {exc.__class__.__name__} ({source})"
    logger.error("Groww %s error source=%s status=%s path=%s error=%r",operation,source,status,upstream_path,exc); raise HTTPException(status_code=429 if status==429 else 502,detail=detail)

class ScanRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE"]); timeframe:TF="15m"; min_risk_reward:float=1.5
class MTFRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE"]); timeframes:list[TF]=Field(default_factory=lambda:["5m","15m","1h"]); min_risk_reward:float=1.5
class ManualGift(BaseModel): ltp:float; change_pct:float; entered_at:str|None=None
class FNORequest(BaseModel): symbol:str="RELIANCE"; timeframes:list[TF]=Field(default_factory=lambda:["5m","15m","1h"]); min_risk_reward:float=1.5; expiry:str|None=None; include_market:bool=True; take_snapshot:bool=True; manual_gift:ManualGift|None=None
class SnapshotRequest(BaseModel): symbol:str="RELIANCE"; expiry:str|None=None
class BacktestRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE"]); start_date:str; end_date:str; min_risk_reward:float=1.5; entry_before:str|None=None
class StrategyResearchRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK"]); start_date:str; end_date:str; target_r:float=1.0
class SetupDiscoveryV2Request(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","HDFCBANK","ICICIBANK","SBIN","TCS","INFY","TATASTEEL","MARUTI"]); start_date:str; end_date:str
class CandlestickDiscoveryV1Request(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","HDFCBANK","ICICIBANK","SBIN","TCS","INFY","TATASTEEL","MARUTI"]); start_date:str; end_date:str
class StrategyPremiumReplayRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK"]); start_date:str; end_date:str; strategy:Literal["VWAP_TREND","ORB_30","BREAKOUT_20"]="VWAP_TREND"; research_target_r:float=1.0; premium_min_risk_reward:float=1.5; max_trades:int=30
class OptionNativeResearchRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK","HDFCBANK","ICICIBANK","TATASTEEL","HINDALCO","ONGC","INFY","TCS"]); start_date:str; end_date:str; research_target_r:float=1.0; premium_min_risk_reward:float=1.5; max_trades_per_strategy:int=30; round_trip_cost_bps:float=10.0
class OptionNativePhase2Request(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK","HDFCBANK","ICICIBANK","TATASTEEL","HINDALCO","ONGC","INFY","TCS"]); start_date:str; end_date:str; premium_min_risk_reward:float=1.5; max_trades_per_model:int=30; round_trip_cost_bps:float=10.0
class MarketRegimeResearchRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK","HDFCBANK","ICICIBANK","TATASTEEL","HINDALCO","ONGC","INFY","TCS"]); start_date:str; end_date:str; premium_min_risk_reward:float=1.5; max_trades_per_model:int=30; round_trip_cost_bps:float=10.0
class EdgeDiscoveryRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK","HDFCBANK","ICICIBANK","TATASTEEL","HINDALCO","ONGC","INFY","TCS"]); start_date:str; end_date:str; max_observations:int=600; round_trip_cost_bps:float=10.0; sample_every_bars:int=3
class CandidateValidatorRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["MARUTI","EICHERMOT","INDUSINDBK","JSWSTEEL","TITAN","NESTLEIND","GRASIM","BRITANNIA","LT","DRREDDY","BAJFINANCE","M&M","SUNPHARMA","ADANIPORTS","KOTAKBANK"]); start_date:str; end_date:str; round_trip_cost_bps:float=10.0; sample_every_bars:int=3; max_trades:int=250
class CandidateBValidatorRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK","HDFCBANK","ICICIBANK","TATASTEEL","HINDALCO","ONGC","INFY","TCS"]); start_date:str; end_date:str; round_trip_cost_bps:float=10.0; sample_every_bars:int=3; max_trades:int=250
class CandidateHOptionRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","HDFCBANK","ICICIBANK","SBIN","TCS","INFY","TATASTEEL","MARUTI","AXISBANK","KOTAKBANK","LT","HINDALCO"]); start_date:str; end_date:str; max_signals:int=80
class MarketBrainContextBlockRequest(BaseModel): start_date:str; end_date:str; min_obs:int=20
class MarketBrainSetupExpectancyRequest(BaseModel): start_date:str; end_date:str
class MarketBrainV7ObservationRequest(BaseModel): start_date:str; end_date:str; role:Literal["DEVELOPMENT","HOLDOUT"]
class MarketBrainV7EvaluateRequest(BaseModel): development:list[dict]; holdout:list[dict]
class FNOHistoryProbeRequest(BaseModel): symbol:str="RELIANCE"; expiry:str; strike:float; option_type:Literal["CE","PE"]; interval:Literal["1minute","5minute","10minute","15minute","30minute","1hour","1day"]="5minute"; lookback_days:int=5
class FNOPremiumReplayRequest(BaseModel): symbol:str="RELIANCE"; expiry:str; strike:float; option_type:Literal["CE","PE"]; trade_date:str; entry_time:str="09:30"; min_risk_reward:float=1.5
class FNOTrueBacktestRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE"]); start_date:str; end_date:str; expiry:str|None=None; min_risk_reward:float=1.5; entry_before:str|None=None; max_trades:int=20
class CommodityBacktestRequest(BaseModel): symbol:Literal["CRUDEOIL","NATURALGAS"]; days:int=30; min_risk_reward:float=1.5; strength_threshold:float=65.0; slippage_bps:float=2.0; cost_bps:float=2.0

@app.get("/")
async def root(): return {"ok":True,"service":"alphapilot-api"}
@app.get("/health")
async def health(): return {"ok":True,"service":"alphapilot-api","version":"0.28.3","provider":settings.market_data_provider.upper()}
@app.get("/v1/market/global-intelligence")
async def market_global_intelligence(limit:int=5): return await global_intelligence(limit)
@app.get("/v1/quote/{symbol}")
async def quote(symbol:str):
    try:return await get_provider(settings).quote(symbol.upper())
    except Exception as exc:_safe_upstream_error("quote",exc)
@app.get("/v1/candles/{symbol}")
async def candles(symbol:str,timeframe:TF="15m"):
    try:return {"symbol":symbol.upper(),"timeframe":timeframe,"candles":await get_provider(settings).candles(symbol.upper(),timeframe)}
    except Exception as exc:_safe_upstream_error("candles",exc)
@app.get("/v1/options/{symbol}")
async def options(symbol:str,expiry:str|None=None):
    try:return await get_provider(settings).option_chain(symbol.upper(),expiry)
    except Exception as exc:_safe_upstream_error("option chain",exc)
@app.post("/v1/research/strategies")
async def strategy_research(request:StrategyResearchRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_strategy_research(get_provider(settings),symbols,request.start_date,request.end_date,request.target_r)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("strategy research",exc)
@app.post("/v1/research/setup-discovery-v2")
async def setup_discovery_v2(request:SetupDiscoveryV2Request):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_setup_discovery_v2(get_provider(settings),symbols,request.start_date,request.end_date)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("setup discovery v2",exc)
@app.post("/v1/research/candlestick-discovery-v1")
@app.post("/v1/research/candlestick-discovery")
async def candlestick_discovery_v1(request:CandlestickDiscoveryV1Request):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_candlestick_research(get_provider(settings),symbols,request.start_date,request.end_date)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("candlestick discovery v1",exc)
@app.post("/v1/research/candlestick-discovery-v2")
async def candlestick_discovery_v2(request:CandlestickDiscoveryV1Request):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_candlestick_research_v2(get_provider(settings),symbols,request.start_date,request.end_date)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("candlestick discovery v2",exc)
@app.post("/v1/research/strategy-premium")
async def strategy_premium_replay(request:StrategyPremiumReplayRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_strategy_premium_replay(get_provider(settings),symbols,request.start_date,request.end_date,request.strategy,request.research_target_r,request.premium_min_risk_reward,request.max_trades)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("strategy premium replay",exc)
@app.post("/v1/research/option-native")
async def option_native_research(request:OptionNativeResearchRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_option_native_research(get_provider(settings),symbols,request.start_date,request.end_date,request.research_target_r,request.premium_min_risk_reward,request.max_trades_per_strategy,request.round_trip_cost_bps)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("option-native research",exc)
@app.post("/v1/research/option-native/phase2")
async def option_native_phase2(request:OptionNativePhase2Request):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_option_native_phase2(get_provider(settings),symbols,request.start_date,request.end_date,request.premium_min_risk_reward,request.max_trades_per_model,request.round_trip_cost_bps)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("option-native phase 2",exc)
@app.post("/v1/research/market-regime")
async def market_regime_research(request:MarketRegimeResearchRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_market_regime_research(get_provider(settings),symbols,request.start_date,request.end_date,request.premium_min_risk_reward,request.max_trades_per_model,request.round_trip_cost_bps)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("market regime research",exc)
@app.post("/v1/research/edge-discovery")
async def edge_discovery(request:EdgeDiscoveryRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_edge_discovery(get_provider(settings),symbols,request.start_date,request.end_date,request.max_observations,request.round_trip_cost_bps,request.sample_every_bars)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("edge discovery",exc)
@app.post("/v1/research/market-brain-v3-block")
async def market_brain_v3_block(request:MarketBrainContextBlockRequest):
    try:return await run_market_brain_context_block(get_provider(settings),request.start_date,request.end_date,request.min_obs)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("market brain v3 replication",exc)
@app.post("/v1/research/market-brain-v4-setup-expectancy")
async def market_brain_v4_setup_expectancy(request:MarketBrainSetupExpectancyRequest):
    try:return await run_market_brain_setup_expectancy(get_provider(settings),request.start_date,request.end_date)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("market brain v4 setup expectancy",exc)
@app.post("/v1/research/market-brain-v6-dynamic-context")
async def market_brain_v6_dynamic_context(request:MarketBrainSetupExpectancyRequest):
    try:return await run_market_brain_setup_expectancy(get_provider(settings),request.start_date,request.end_date)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("market brain v6 dynamic context",exc)
@app.post("/v1/research/market-brain-v7-observations")
async def market_brain_v7_observations(request:MarketBrainV7ObservationRequest):
    try:return await run_market_brain_v7_observations(get_provider(settings),request.start_date,request.end_date,request.role)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("market brain v7 observations",exc)
@app.post("/v1/research/market-brain-v7-evaluate")
async def market_brain_v7_evaluate(request:MarketBrainV7EvaluateRequest):
    try:return evaluate_market_brain_v7(request.development,request.holdout)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("market brain v7 evaluation",exc)
@app.post("/v1/research/candidate-validator")
async def candidate_validator(request:CandidateValidatorRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_candidate_validator(get_provider(settings),symbols,request.start_date,request.end_date,request.round_trip_cost_bps,request.sample_every_bars,request.max_trades)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("candidate validator",exc)
@app.post("/v1/research/candidate-b-validator")
async def candidate_b_validator(request:CandidateBValidatorRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_candidate_b_validator(get_provider(settings),symbols,request.start_date,request.end_date,request.round_trip_cost_bps,request.sample_every_bars,request.max_trades)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("candidate B validator",exc)
@app.post("/v1/research/candidate-h-option-oos")
async def candidate_h_option_oos(request:CandidateHOptionRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_candidate_h_option_validator(get_provider(settings),symbols,request.start_date,request.end_date,request.max_signals)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("Candidate H option OOS",exc)
@app.post("/v1/fno/history/probe")
async def fno_history_probe(request:FNOHistoryProbeRequest):
    try:return await probe_historical_option_candles(get_provider(settings),request.symbol.upper(),request.expiry,request.strike,request.option_type,request.interval,request.lookback_days)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("historical F&O option probe",exc)
@app.post("/v1/fno/history/replay")
async def fno_history_replay(request:FNOPremiumReplayRequest):
    try:return await replay_option_trade(get_provider(settings),request.symbol.upper(),request.expiry,request.strike,request.option_type,request.trade_date,request.entry_time,request.min_risk_reward)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("historical F&O premium replay",exc)
@app.post("/v1/fno/backtest/premium")
async def fno_true_backtest(request:FNOTrueBacktestRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_true_premium_backtest(get_provider(settings),symbols,request.start_date,request.end_date,request.expiry,request.min_risk_reward,request.entry_before,request.max_trades)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("true premium F&O backtest",exc)
@app.get("/v1/commodity/contract/{symbol}")
async def commodity_contract(symbol:str):return await resolve_nearest_mcx_future(symbol.upper())
@app.get("/v1/commodity/quote/{symbol}")
async def mcx_quote(symbol:str):return await commodity_quote(get_provider(settings),symbol.upper())
@app.get("/v1/commodity/candles/{symbol}")
async def mcx_candles(symbol:str,timeframe:Literal["5m","15m","1h"]="5m"):return await commodity_candles(get_provider(settings),symbol.upper(),timeframe)
@app.get("/v1/commodity/probe/{symbol}")
async def mcx_probe(symbol:str):return await commodity_probe(get_provider(settings),symbol.upper())
@app.get("/v1/commodity/scan/{symbol}")
async def mcx_scan(symbol:str,min_risk_reward:float=1.5):return await commodity_mtf_scan(get_provider(settings),symbol.upper(),min_risk_reward)
@app.get("/v1/commodity/news/{symbol}")
async def mcx_news(symbol:str,limit:int=4):return await latest_commodity_news(symbol.upper(),max(1,min(int(limit),6)))
@app.post("/v1/commodity/backtest")
async def mcx_backtest(request:CommodityBacktestRequest):return await run_commodity_backtest(get_provider(settings),request.symbol,request.days,request.min_risk_reward,request.strength_threshold,request.slippage_bps,request.cost_bps)
@app.post("/v1/commodity/backtest/continuous")
async def mcx_continuous_backtest(request:CommodityBacktestRequest):return await run_continuous_commodity_backtest(get_provider(settings),request.symbol,request.days,request.min_risk_reward,request.strength_threshold,request.slippage_bps,request.cost_bps)
@app.get("/v1/news")
async def news(symbols:str,limit:int=3):return await latest_market_news([x.strip().upper() for x in symbols.split(",") if x.strip()],max(1,min(int(limit),5)))
@app.post("/v1/scan")
async def scan(request:ScanRequest):return await get_provider(settings).scan(request.symbols,request.timeframe,request.min_risk_reward)
@app.post("/v1/scan/mtf")
async def mtf(request:MTFRequest):
    symbols=[s.upper() for s in request.symbols]
    try:return await get_provider(settings).multi_timeframe_scan(symbols,request.timeframes,request.min_risk_reward)
    except Exception as exc:logger.error("MTF request crashed symbols=%s error=%s\n%s",symbols,repr(exc),traceback.format_exc());raise
@app.post("/v1/backtest")
async def backtest(request:BacktestRequest):return await run_backtest(get_provider(settings),[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"],request.start_date,request.end_date,request.min_risk_reward,request.entry_before)
@app.get("/v1/market/context")
async def market_context(timeframes:str="5m,15m,1h"):
    parsed=[x.strip() for x in timeframes.split(",") if x.strip() in {"5m","15m","1h","1d"}] or ["5m","15m","1h"];return await get_provider(settings).market_context(parsed)
@app.get("/v1/context/external/{symbol}")
async def external_context(symbol:str):return await external_market_context(symbol.upper())
@app.post("/v1/fno/snapshot")
async def fno_snapshot(request:SnapshotRequest):return await get_provider(settings).take_option_snapshot(request.symbol.upper(),request.expiry)
@app.post("/v1/scan/fno")
async def fno(request:FNORequest):
    manual=request.manual_gift.model_dump() if request.manual_gift else None
    return await get_provider(settings).fno_confirm(request.symbol.upper(),request.timeframes,request.min_risk_reward,request.expiry,request.include_market,request.take_snapshot,manual)
