from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from typing import Literal
import logging
import hmac
import traceback

from .backtest import run_backtest
from .candidate_validator import run_candidate_validator
from .candidate_b_validator import run_candidate_b_validator
from .candidate_h_option_validator import run_candidate_h_option_validator
from .candlestick_research import run_candlestick_research
from .candlestick_research_v2 import run_candlestick_research_v2
from .commodity_backtest import run_commodity_backtest
from .copper_research_brain import run_copper_research_baseline, run_copper_brain_b_experiment, run_copper_edge_attribution, run_copper_regime_stability, run_copper_regime_stability_from_store, run_copper_expanding_daily_edge_from_store, run_copper_interaction_stability_from_store
from .copper_avoidance_forward_validation import run_copper_avoidance_forward_validation_from_store
from .copper_day_replay import run_copper_day_by_day_replay_from_store
from .commodity_candle_collector import PostgresCandleStore, backfill_commodity_candles, backfill_continuous_commodity_candles, collect_completed_commodity_candles
from .historical_context import PostgresHistoricalContextStore
from .copper_context_ablation import build_copper_context_coverage_for_days
from .copper_context_ablation_v2 import context_ablation
from .copper_context_feature_audit import descriptive_context_features
from .copper_context_interaction_audit import descriptive_context_interactions
from .copper_fx_level_downtrend_forward_validation import validate_fx_level_downtrend
from .commodity_continuous_backtest import discover_groww_historical_mcx_contracts, run_continuous_commodity_backtest
from .commodity_click_replay import audit_identified_setups, run_frozen_extended_click_backtest, run_frozen_july_validation_backtest, run_frozen_tuesday_phase_a, run_frozen_weekly_click_backtest, validate_frozen_tuesday_phase_a_data
from .commodity_live import run_commodity_live_scan
from .commodity_next_session import run_commodity_next_session
from .commodity_option_history import probe_mcx_option_history, scan_mcx_option_history_band
from .commodity_option_candle_collector import PostgresOptionCandleStore, collect_copper_option_candles
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
from .risk_discipline import RiskDisciplineRequest, evaluate_risk_discipline
from .paper_session_quality import PaperSessionAttestationRequest, evaluate_paper_session
from .paper_trade_lifecycle import (
    ExactOptionContract,
    PaperTradeMarkRequest,
    PaperTradeOpenRequest,
    fetch_live_option_observation,
    mark_paper_trade,
    open_paper_trade,
)
from .pullback_short_option_h1 import run_pullback_short_option_h1
from .setup_discovery_v2 import run_setup_discovery_v2
from .setup_discovery_v3 import run_setup_discovery_v3
from .session_close_momentum import run_session_close_momentum
from .strategy_research import run_strategy_research
from .strategy_premium_replay import run_strategy_premium_replay
from .strategy_regime_routing import run_strategy_regime_routing

logger = logging.getLogger("alphapilot.scan")
class Settings(BaseSettings):
    market_data_provider: str = "MOCK"
    allowed_origins: str = "*"
    database_url: str = ""
    commodity_collector_token: str = ""
    class Config: env_file = ".env"
settings=Settings(); app=FastAPI(title="AlphaPilot API",version="0.40.0"); parsed_origins=[x.strip() for x in settings.allowed_origins.split(",") if x.strip()]; parsed_origins=["*"] if "*" in parsed_origins else parsed_origins; app.add_middleware(CORSMiddleware,allow_origins=parsed_origins,allow_credentials=False,allow_methods=["*"],allow_headers=["*"]); TF=Literal["5m","15m","1h","1d"]

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
class StrategyPremiumReplayRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK"]); start_date:str; end_date:str; strategy:Literal["VWAP_TREND","ORB_30","BREAKOUT_20","PRICE_ACTION_BREAKOUT"]="VWAP_TREND"; research_target_r:float=1.0; premium_min_risk_reward:float=1.5; max_trades:int=30
class OptionNativeResearchRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK","HDFCBANK","ICICIBANK","TATASTEEL","HINDALCO","ONGC","INFY","TCS"]); start_date:str; end_date:str; research_target_r:float=1.0; premium_min_risk_reward:float=1.5; max_trades_per_strategy:int=30; round_trip_cost_bps:float=10.0
class StrategyRegimeRoutingRequest(BaseModel): symbols:list[str]=Field(default_factory=lambda:["RELIANCE","SBIN","AXISBANK","HDFCBANK","ICICIBANK","TATASTEEL","HINDALCO","ONGC","INFY","TCS"]); development_start:str; development_end:str; holdout_start:str; holdout_end:str; research_target_r:float=1.0; premium_min_risk_reward:float=1.5; max_trades_per_strategy:int=50; round_trip_cost_bps:float=10.0
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
class CommodityBacktestRequest(BaseModel): symbol:Literal["COPPER","CRUDEOIL","NATURALGAS"]; days:int=30; min_risk_reward:float=1.5; strength_threshold:float=65.0; slippage_bps:float=2.0; cost_bps:float=2.0
class CopperResearchBaselineRequest(BaseModel): days:int=30; sample_every_bars:int=3; round_trip_cost_bps:float=4.0
class CommodityCandleBackfillRequest(BaseModel): symbol:Literal["COPPER","CRUDEOIL","NATURALGAS"]; start_at:str; end_at:str; timeframe_minutes:Literal[5,15,60]=5
class CommodityNextSessionRequest(BaseModel): observation_date:str; target_date:str; include_outcome:bool=False; include_news:bool=True
class CommodityOptionHistoryProbeRequest(BaseModel): symbol:Literal["COPPER","CRUDEOIL","NATURALGAS"]; trade_date:str; underlying_price:float=Field(gt=0); option_type:Literal["CE","PE"]
class CommodityOptionHistoryBandRequest(BaseModel): symbol:Literal["COPPER","CRUDEOIL","NATURALGAS"]; trade_date:str; center_price:float=Field(gt=0); radius:int=Field(default=5,ge=0,le=8)

@app.get("/")
async def root(): return {"ok":True,"service":"alphapilot-api"}
@app.get("/health")
async def health(): return {"ok":True,"service":"alphapilot-api","version":"0.40.0","provider":settings.market_data_provider.upper(),"commodity_collector_enabled":bool(settings.database_url.strip() and settings.commodity_collector_token.strip())}
def _collector_store(x_collector_token:str|None):
    expected=settings.commodity_collector_token.strip(); supplied=str(x_collector_token or "")
    if not settings.database_url.strip() or not expected:
        raise HTTPException(status_code=503,detail={"code":"COLLECTOR_DISABLED","message":"Configure DATABASE_URL and COMMODITY_COLLECTOR_TOKEN to enable collection"})
    if not hmac.compare_digest(supplied,expected):
        raise HTTPException(status_code=401,detail="Invalid collector token")
    return PostgresCandleStore(settings.database_url)
@app.get("/v1/internal/commodity-contracts/historical-capability")
async def commodity_contracts_historical_capability(symbol:str="COPPER",days:int=180,x_collector_token:str|None=Header(default=None)):
    _collector_store(x_collector_token)
    try:
        from datetime import datetime,timedelta
        from zoneinfo import ZoneInfo
        end=datetime.now(ZoneInfo("Asia/Kolkata"))
        start=end-timedelta(days=max(30,min(int(days),365)))
        result=await discover_groww_historical_mcx_contracts(get_provider(settings),symbol,start,end)
        return {
            "symbol":symbol.upper(),
            "requested_days":max(30,min(int(days),365)),
            **result,
        }
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("historical MCX contract discovery",exc)

@app.post("/v1/internal/commodity-candles/backfill-continuous")
async def commodity_candles_backfill_continuous(request:CommodityCandleBackfillRequest,x_collector_token:str|None=Header(default=None)):
    store=_collector_store(x_collector_token)
    try:
        from datetime import datetime
        return await backfill_continuous_commodity_candles(get_provider(settings),store,request.symbol,datetime.fromisoformat(request.start_at),datetime.fromisoformat(request.end_at),request.timeframe_minutes)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("continuous commodity candle backfill",exc)
@app.post("/v1/internal/commodity-candles/backfill")
async def commodity_candles_backfill(request:CommodityCandleBackfillRequest,x_collector_token:str|None=Header(default=None)):
    store=_collector_store(x_collector_token)
    try:
        from datetime import datetime
        return await backfill_commodity_candles(get_provider(settings),store,request.symbol,datetime.fromisoformat(request.start_at),datetime.fromisoformat(request.end_at),request.timeframe_minutes)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("commodity candle backfill",exc)
@app.post("/v1/internal/commodity-candles/collect")
async def commodity_candles_collect(x_collector_token:str|None=Header(default=None)):
    store=_collector_store(x_collector_token)
    try:return await collect_completed_commodity_candles(get_provider(settings),store)
    except Exception as exc:_safe_upstream_error("commodity candle collection",exc)
@app.get("/v1/internal/commodity-candles/status")
async def commodity_candles_status(x_collector_token:str|None=Header(default=None)):
    store=_collector_store(x_collector_token)
    try:return await store.status()
    except Exception as exc:_safe_upstream_error("commodity candle storage status",exc)
@app.post("/v1/internal/commodity-options/collect")
async def commodity_options_collect(
    strikes_per_type:int=12,
    x_collector_token:str|None=Header(default=None),
):
    candle_store=_collector_store(x_collector_token)
    try:
        option_store=PostgresOptionCandleStore(settings.database_url)
        return await collect_copper_option_candles(
            get_provider(settings),
            candle_store,
            option_store,
            strikes_per_type=max(1,min(int(strikes_per_type),20)),
        )
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("Copper option candle collection",exc)

@app.get("/v1/internal/commodity-options/status")
async def commodity_options_status(
    symbol:str="COPPER",
    x_collector_token:str|None=Header(default=None),
):
    _collector_store(x_collector_token)
    try:
        store=PostgresOptionCandleStore(settings.database_url)
        await store.initialize()
        return await store.status(symbol.upper())
    except Exception as exc:_safe_upstream_error("commodity option candle storage status",exc)

@app.post("/v1/risk/discipline/evaluate")
async def risk_discipline_evaluate(request:RiskDisciplineRequest):
    try:return evaluate_risk_discipline(request)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
@app.post("/v1/paper-trades/open")
async def paper_trade_open(request:PaperTradeOpenRequest):
    try:
        observation=await fetch_live_option_observation(get_provider(settings),request.contract)
        return open_paper_trade(request.risk_request,request.contract,observation)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("paper option observation",exc)

@app.post("/v1/paper-trades/mark")
async def paper_trade_mark(request:PaperTradeMarkRequest):
    try:
        contract=request.paper_trade
        exact=ExactOptionContract(symbol=contract.symbol,expiry=contract.expiry,strike=contract.strike,option_type=contract.option_type,lot_size=contract.lot_size)
        observation=await fetch_live_option_observation(get_provider(settings),exact)
        return mark_paper_trade(request.paper_trade,observation,request.manual_exit)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("paper option observation",exc)

@app.post("/v1/paper-sessions/attest")
async def paper_session_attest(request:PaperSessionAttestationRequest):
    return evaluate_paper_session(request)

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
@app.post("/v1/research/setup-discovery-v3")
async def setup_discovery_v3(request:SetupDiscoveryV2Request):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_setup_discovery_v3(get_provider(settings),symbols,request.start_date,request.end_date)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("setup discovery v3",exc)
@app.post("/v1/research/session-close-momentum-v1")
async def session_close_momentum_v1(request:MarketBrainSetupExpectancyRequest):
    try:return await run_session_close_momentum(get_provider(settings),request.start_date,request.end_date)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("session-close momentum v1",exc)
@app.post("/v1/research/pullback-short-option-h1")
async def pullback_short_option_h1():
    try:return await run_pullback_short_option_h1(get_provider(settings))
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("pullback short option H-1",exc)
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
@app.post("/v1/research/strategy-regime-routing-v1")
async def strategy_regime_routing_v1(request:StrategyRegimeRoutingRequest):
    symbols=[s.upper() for s in request.symbols if s.strip()] or ["RELIANCE"]
    try:return await run_strategy_regime_routing(get_provider(settings),symbols,request.development_start,request.development_end,request.holdout_start,request.holdout_end,request.research_target_r,request.premium_min_risk_reward,request.max_trades_per_strategy,request.round_trip_cost_bps)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("strategy regime routing v1",exc)
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
@app.post("/v1/research/copper/baseline-v1")
async def copper_research_baseline(request:CopperResearchBaselineRequest):
    try:return await run_copper_research_baseline(get_provider(settings),request.days,request.sample_every_bars,request.round_trip_cost_bps)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("Copper research baseline",exc)
@app.post("/v1/research/copper/brain-b-v1")
async def copper_brain_b_v1(request:CopperResearchBaselineRequest):
    try:return await run_copper_brain_b_experiment(get_provider(settings),request.days,request.sample_every_bars,request.round_trip_cost_bps)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("Copper Brain B experiment",exc)
@app.post("/v1/research/copper/edge-attribution-v1")
async def copper_edge_attribution_v1(request:CopperResearchBaselineRequest):
    try:return await run_copper_edge_attribution(get_provider(settings),request.days,request.sample_every_bars,request.round_trip_cost_bps)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("Copper edge attribution",exc)
@app.post("/v1/research/copper/day-by-day-capital-replay-v1")
async def copper_day_by_day_capital_replay_v1():
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL to enable stored Copper replay"})
    try:
        return await run_copper_day_by_day_replay_from_store(
            PostgresCandleStore(settings.database_url),
        )
    except Exception as exc:_safe_upstream_error("Copper day-by-day capital replay",exc)

@app.post("/v1/research/copper/avoidance-forward-validation-v1")
async def copper_avoidance_forward_validation_v1(request:CopperResearchBaselineRequest):
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL to enable stored Copper research"})
    try:
        return await run_copper_avoidance_forward_validation_from_store(
            PostgresCandleStore(settings.database_url),
            request.days,
            request.sample_every_bars,
        )
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("Copper avoidance forward validation",exc)
@app.post("/v1/research/copper/interaction-stability-stored-v1")
async def copper_interaction_stability_stored_v1(request:CopperResearchBaselineRequest):
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL to enable stored Copper research"})
    try:
        return await run_copper_interaction_stability_from_store(
            PostgresCandleStore(settings.database_url),
            request.days,
            request.sample_every_bars,
            request.round_trip_cost_bps,
            4,
            15,
        )
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("stored Copper interaction stability",exc)
@app.post("/v1/research/copper/expanding-daily-edge-stored-v1")
async def copper_expanding_daily_edge_stored_v1(request:CopperResearchBaselineRequest):
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL to enable stored Copper research"})
    try:
        return await run_copper_expanding_daily_edge_from_store(
            PostgresCandleStore(settings.database_url),
            days=request.days,
            sample_every_bars=request.sample_every_bars,
            round_trip_cost_bps=request.round_trip_cost_bps,
            minimum_training_signals=20,
        )
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("stored Copper expanding daily edge",exc)

@app.post("/v1/research/copper/expanding-daily-edge-context-audit-v1")
async def copper_expanding_daily_edge_context_audit_v1(request:CopperResearchBaselineRequest):
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL to enable stored Copper research"})
    try:
        replay=await run_copper_expanding_daily_edge_from_store(
            PostgresCandleStore(settings.database_url),
            days=request.days,
            sample_every_bars=request.sample_every_bars,
            round_trip_cost_bps=request.round_trip_cost_bps,
            minimum_training_signals=20,
        )
        test_days=[row["test_day"] for row in replay["backtest"]["daily_results"]]
        context_store=PostgresHistoricalContextStore(settings.database_url)
        context_store.initialize()
        audit=build_copper_context_coverage_for_days(context_store,test_days,decision_hour=10)
        return {
            "mode":"ALPHAPILOT_COPPER_EXPANDING_CONTEXT_AUDIT_V1",
            "research_only":True,
            "production_rules_changed":False,
            "decision_time_policy":"10:00 Asia/Kolkata for day-level context coverage audit",
            "replay_summary":{
                "calendar_days_observed":replay["backtest"]["calendar_days_observed"],
                "test_days":replay["backtest"]["test_days"],
                "aggregate":replay["backtest"]["aggregate"],
            },
            "context_coverage":audit,
            "guardrail":"Context is reported only when available_at <= simulated decision time. It does not affect the hypothesis in this audit.",
        }
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("stored Copper context coverage audit",exc)

@app.post("/v1/research/copper/context-ablation-v1")
async def copper_context_ablation_v1(request:CopperResearchBaselineRequest):
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL"})
    try:
        from datetime import datetime,timedelta
        from zoneinfo import ZoneInfo
        from .copper_research_brain import build_copper_experiences
        candle_store=PostgresCandleStore(settings.database_url)
        await candle_store.initialize()
        end=datetime.now(ZoneInfo("Asia/Kolkata")); start=end-timedelta(days=max(7,min(request.days,3650)))
        segments=await candle_store.read_symbol_contract_segments("COPPER",5,start,end)
        experiences=[]
        for segment in segments:
            experiences.extend(build_copper_experiences(segment.get("candles") or [],sample_every_bars=request.sample_every_bars))
        experiences.sort(key=lambda x:str((x.get("features") or {}).get("timestamp") or ""))
        context_store=PostgresHistoricalContextStore(settings.database_url); context_store.initialize()
        return context_ablation(experiences,context_store,60,request.round_trip_cost_bps,20)
    except Exception as exc:_safe_upstream_error("Copper context ablation",exc)

@app.post("/v1/research/copper/context-feature-audit-v1")
async def copper_context_feature_audit_v1(request:CopperResearchBaselineRequest):
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL"})
    try:
        from datetime import datetime,timedelta
        from zoneinfo import ZoneInfo
        from .copper_research_brain import build_copper_experiences
        candle_store=PostgresCandleStore(settings.database_url); await candle_store.initialize()
        end=datetime.now(ZoneInfo("Asia/Kolkata")); start=end-timedelta(days=max(7,min(request.days,3650)))
        segments=await candle_store.read_symbol_contract_segments("COPPER",5,start,end)
        experiences=[]
        for segment in segments:experiences.extend(build_copper_experiences(segment.get("candles") or [],sample_every_bars=request.sample_every_bars))
        experiences.sort(key=lambda x:str((x.get("features") or {}).get("timestamp") or ""))
        context_store=PostgresHistoricalContextStore(settings.database_url); context_store.initialize()
        return descriptive_context_features(experiences,context_store,60,request.round_trip_cost_bps)
    except Exception as exc:_safe_upstream_error("Copper context feature audit",exc)

@app.post("/v1/research/copper/context-interaction-audit-v1")
async def copper_context_interaction_audit_v1(request:CopperResearchBaselineRequest):
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL"})
    try:
        from datetime import datetime,timedelta
        from zoneinfo import ZoneInfo
        from .copper_research_brain import build_copper_experiences
        candle_store=PostgresCandleStore(settings.database_url); await candle_store.initialize()
        end=datetime.now(ZoneInfo("Asia/Kolkata")); start=end-timedelta(days=max(7,min(request.days,3650)))
        segments=await candle_store.read_symbol_contract_segments("COPPER",5,start,end)
        experiences=[]
        for segment in segments:experiences.extend(build_copper_experiences(segment.get("candles") or [],sample_every_bars=request.sample_every_bars))
        experiences.sort(key=lambda x:str((x.get("features") or {}).get("timestamp") or ""))
        context_store=PostgresHistoricalContextStore(settings.database_url); context_store.initialize()
        return descriptive_context_interactions(experiences,context_store,60,request.round_trip_cost_bps,8)
    except Exception as exc:_safe_upstream_error("Copper context interaction audit",exc)

@app.post("/v1/research/copper/fx-level-downtrend-forward-validation-v1")
async def copper_fx_level_downtrend_forward_validation_v1(request:CopperResearchBaselineRequest):
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL"})
    try:
        from datetime import datetime,timedelta
        from zoneinfo import ZoneInfo
        from .copper_research_brain import build_copper_experiences
        candle_store=PostgresCandleStore(settings.database_url); await candle_store.initialize()
        end=datetime.now(ZoneInfo("Asia/Kolkata")); start=end-timedelta(days=max(7,min(request.days,3650)))
        segments=await candle_store.read_symbol_contract_segments("COPPER",5,start,end)
        experiences=[]
        for segment in segments:experiences.extend(build_copper_experiences(segment.get("candles") or [],sample_every_bars=request.sample_every_bars))
        experiences.sort(key=lambda x:str((x.get("features") or {}).get("timestamp") or ""))
        context_store=PostgresHistoricalContextStore(settings.database_url); context_store.initialize()
        return validate_fx_level_downtrend(experiences,context_store,60,request.round_trip_cost_bps)
    except Exception as exc:_safe_upstream_error("Copper FX level downtrend forward validation",exc)

@app.post("/v1/research/copper/regime-stability-stored-v1")
async def copper_regime_stability_stored_v1(request:CopperResearchBaselineRequest):
    if not settings.database_url.strip():
        raise HTTPException(status_code=503,detail={"code":"RESEARCH_STORE_DISABLED","message":"Configure DATABASE_URL to enable stored Copper research"})
    try:return await run_copper_regime_stability_from_store(PostgresCandleStore(settings.database_url),request.days,request.sample_every_bars,request.round_trip_cost_bps,4)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("stored Copper regime stability",exc)
@app.post("/v1/research/copper/regime-stability-v1")
async def copper_regime_stability_v1(request:CopperResearchBaselineRequest):
    try:return await run_copper_regime_stability(get_provider(settings),request.days,request.sample_every_bars,request.round_trip_cost_bps,4)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("Copper regime stability",exc)
@app.post("/v1/commodity/backtest/continuous")
async def mcx_continuous_backtest(request:CommodityBacktestRequest):return await run_continuous_commodity_backtest(get_provider(settings),request.symbol,request.days,request.min_risk_reward,request.strength_threshold,request.slippage_bps,request.cost_bps)
@app.post("/v1/commodity/next-session-prototype-v1")
async def mcx_next_session_prototype(request:CommodityNextSessionRequest):
    try:return await run_commodity_next_session(get_provider(settings),request.observation_date,request.target_date,request.include_outcome,request.include_news)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("commodity next-session prototype",exc)
@app.post("/v1/research/commodity-option-history-probe")
async def mcx_option_history_probe(request:CommodityOptionHistoryProbeRequest):
    try:return await probe_mcx_option_history(get_provider(settings),request.symbol,request.trade_date,request.underlying_price,request.option_type)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("historical MCX option premium probe",exc)
@app.post("/v1/research/commodity-option-history-band")
async def mcx_option_history_band(request:CommodityOptionHistoryBandRequest):
    try:return await scan_mcx_option_history_band(get_provider(settings),request.symbol,request.trade_date,request.center_price,request.radius)
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    except Exception as exc:_safe_upstream_error("historical MCX option premium band scan",exc)
@app.post("/v1/research/commodity-click-phase-a-tuesday-v1")
async def commodity_click_phase_a_tuesday():
    try:return await run_frozen_tuesday_phase_a(get_provider(settings))
    except Exception as exc:_safe_upstream_error("commodity click Phase A replay",exc)
@app.post("/v1/research/commodity-click-phase-a-data-validation-v1")
async def commodity_click_phase_a_data_validation():
    try:return await validate_frozen_tuesday_phase_a_data(get_provider(settings))
    except Exception as exc:_safe_upstream_error("commodity click Phase A data validation",exc)
@app.post("/v1/research/commodity-click-weekly-v1")
async def commodity_click_weekly_v1():
    try:return await run_frozen_weekly_click_backtest(get_provider(settings))
    except Exception as exc:_safe_upstream_error("commodity weekly click backtest",exc)
@app.post("/v1/research/commodity-click-20-session-v1")
async def commodity_click_20_session_v1():
    try:return await run_frozen_extended_click_backtest(get_provider(settings))
    except Exception as exc:_safe_upstream_error("commodity 20-session click backtest",exc)
@app.post("/v1/research/commodity-click-july-validation-v1")
async def commodity_click_july_validation_v1():
    try:return await run_frozen_july_validation_backtest(get_provider(settings))
    except Exception as exc:_safe_upstream_error("commodity July validation backtest",exc)
@app.post("/v1/research/commodity-identified-setup-audit-v1")
async def commodity_identified_setup_audit_v1():
    try:return await audit_identified_setups(get_provider(settings))
    except Exception as exc:_safe_upstream_error("commodity identified setup audit",exc)
@app.post("/v1/research/commodity-live-scan-v1")
async def commodity_live_scan_v1():
    try:return await run_commodity_live_scan(get_provider(settings))
    except Exception as exc:_safe_upstream_error("commodity live prototype",exc)
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
