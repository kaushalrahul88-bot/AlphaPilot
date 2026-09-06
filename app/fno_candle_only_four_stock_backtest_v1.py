"""Candle-only four-stock, 20-session random-click F&O edge replay."""
from __future__ import annotations
import hashlib, math, random, statistics
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo
import httpx

from . import fno_15m_historical_replay_v1 as core
from .fno_15m_candle_checkpoint_v2 import _is_auth_error, _refresh_after_401
from .fno_underlying_random_replay_v1 import _decision_snapshot, _summarize, resolve_underlying_path

IST=ZoneInfo("Asia/Kolkata"); UTC=timezone.utc
PROTOCOL_ID="FNO_CANDLE_ONLY_FOUR_STOCK_20D_V1_2026-09-06"
END_DATE=date(2026,9,4)
FROZEN_STOCKS=(("ONGC","ENERGY"),("LTIM","INFORMATION_TECHNOLOGY"),("SBIN","BANKING"),("SUNPHARMA","PHARMACEUTICALS"))
STOCKS=tuple(x[0] for x in FROZEN_STOCKS)
TIMEFRAMES=("5m","15m","1h")
INTERVAL={"5m":"5minute","15m":"15minute","1h":"1hour"}
TF_MIN={"5m":5,"15m":15,"1h":60}
CHUNK_DAYS={"5m":6,"15m":13,"1h":55}
CLICKS_PER_DAY=20; CLICK_START=time(9,30); CLICK_END=time(14,0); CLICK_STEP=5
ENGINE_KEYS=("alpha_score","signal","price","latest_candle_at","family_scores","ema9","ema20","ema50","ema200","vwap","rsi14","macd","macd_signal","macd_hist","atr14","bollinger_upper","bollinger_mid","bollinger_lower","volume_ratio_raw","volume_ratio_capped","market_structure","recent_support","recent_resistance","distance_to_resistance_atr","distance_to_support_atr","candle_pattern","confirmations","reasons","warnings","clean_candles")

def _stamp(v): return core._stamp(v)
def _merge(rows): return core._merge_candles(rows)
def _num(v):
    try: return math.isfinite(float(v))
    except (TypeError,ValueError,OverflowError): return False

async def _chunk(provider,symbol,tf,start,end):
    ex,seg,_,gs=provider._instrument(symbol)
    if (ex,seg)!=("NSE","CASH"): raise RuntimeError(f"{symbol} not NSE/CASH")
    async def call(a,b):
        throttle=getattr(provider,"_throttle",None)
        if callable(throttle): await throttle()
        async with httpx.AsyncClient(timeout=45) as client:
            return await client.get(f"{provider.BASE_URL}/v1/historical/candles",headers=await provider._headers(),params={
                "exchange":ex,"segment":seg,"groww_symbol":gs,
                "start_time":a.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                "end_time":b.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                "candle_interval":INTERVAL[tf]})
    rows=[]; cursor=start.astimezone(IST); end=end.astimezone(IST); step=timedelta(minutes=TF_MIN[tf])
    while cursor<=end:
        b=min(end,cursor+timedelta(days=CHUNK_DAYS[tf])-step); r=await call(cursor,b)
        if r.status_code==429:
            reg=getattr(provider,"_register_rate_limit",None)
            if callable(reg): await reg()
        if r.status_code==401:
            try: await _refresh_after_401(provider)
            except Exception: r.raise_for_status()
            r=await call(cursor,b)
        try: r.raise_for_status()
        except Exception as exc:
            if _is_auth_error(exc): raise RuntimeError("GROWW_CANDLE_ONLY_BACKTEST_AUTH_FAILED") from exc
            raise
        body=r.json(); payload=body.get("payload",body) if isinstance(body,Mapping) else {}
        rows.extend(payload.get("candles",[]) if isinstance(payload,Mapping) else [])
        if b>=end: break
        cursor=b+step
    return _merge(rows)

def _dates(candles):
    out=set()
    for row in candles:
        if not isinstance(row,(list,tuple)) or not row: continue
        s=_stamp(row[0])
        if s:
            local=s.astimezone(IST)
            if time(9,15)<=local.time()<=time(15,30): out.add(local.date())
    return out

def common_last_20_sessions(five):
    sets=[_dates(five.get(s,[])) for s in STOCKS]
    if any(not x for x in sets): return []
    return sorted(d for d in set.intersection(*sets) if d<=END_DATE)[-20:]

def deterministic_clicks(day):
    pool=[]; x=datetime.combine(day,CLICK_START,tzinfo=IST); end=datetime.combine(day,CLICK_END,tzinfo=IST)
    while x<=end: pool.append(x.astimezone(UTC)); x+=timedelta(minutes=CLICK_STEP)
    seed=int.from_bytes(hashlib.sha256(f"{PROTOCOL_ID}:{day.isoformat()}".encode()).digest()[:8],"big")
    return sorted(random.Random(seed).sample(pool,CLICKS_PER_DAY))

def _audit(technical):
    tfs=technical.get("timeframes") or {}; detail={}; all_core=True
    for tf in TIMEFRAMES:
        p=tfs.get(tf) or {}; flags={}
        for k in ENGINE_KEYS:
            v=p.get(k)
            flags[k]=_num(v) if k in {"alpha_score","price","ema9","ema20","ema50","ema200","vwap","rsi14","macd","macd_signal","macd_hist","atr14","bollinger_upper","bollinger_mid","bollinger_lower","volume_ratio_raw","volume_ratio_capped","recent_support","recent_resistance","distance_to_resistance_atr","distance_to_support_atr","clean_candles"} else v is not None
        core_ok=(str(p.get("status") or "")!="ERROR" and int(p.get("clean_candles") or 0)>=60 and all(flags.get(k) for k in ("alpha_score","ema20","ema50","vwap","rsi14","macd_hist","atr14","market_structure","recent_support","recent_resistance")))
        detail[tf]={"core_complete":core_ok,"clean_candles":p.get("clean_candles"),"feature_flags":flags}; all_core &= core_ok
    return {"all_three_timeframes_core_complete":bool(all_core),"timeframes":detail}

def _future_bars(candles,click):
    eod=datetime.combine(click.astimezone(IST).date(),time(15,30),tzinfo=IST).astimezone(UTC); out=[]
    for row in candles:
        if not isinstance(row,(list,tuple)) or len(row)<5: continue
        s=_stamp(row[0])
        if s and click<=s and s+timedelta(minutes=5)<=eod: out.append((s,list(row)))
    return [r for _,r in sorted(out,key=lambda x:x[0])]

def _barrier(candles,click,d):
    if d.get("action") not in {"LONG","SHORT"}: return {"status":"NOT_ACTIONABLE"}
    vals=[d.get("model_entry"),d.get("model_stop_loss"),d.get("model_target1"),d.get("model_target2")]
    if not all(_num(x) for x in vals): return {"status":"MODEL_LEVELS_UNAVAILABLE"}
    entry,sl,t1,t2=map(float,vals); direction=d["action"]; risk=(entry-sl if direction=="LONG" else sl-entry)
    if risk<=0: return {"status":"INVALID_MODEL_RISK"}
    first_sl=first_t1=first_t2=None; bars=_future_bars(candles,click)
    for row in bars:
        s=_stamp(row[0]); hi=float(row[2]); lo=float(row[3])
        slh=lo<=sl if direction=="LONG" else hi>=sl
        t1h=hi>=t1 if direction=="LONG" else lo<=t1
        t2h=hi>=t2 if direction=="LONG" else lo<=t2
        if slh and first_sl is None: first_sl=s
        if t1h and first_t1 is None: first_t1=s
        if t2h and first_t2 is None: first_t2=s
    if first_sl and first_t1 and first_sl==first_t1: first="AMBIGUOUS_SL_T1_SAME_5M_BAR"
    elif first_t1 and (not first_sl or first_t1<first_sl): first="T1_BEFORE_SL"
    elif first_sl and (not first_t1 or first_sl<first_t1): first="SL_BEFORE_T1"
    else: first="NEITHER"
    mtm=None
    if bars:
        close=float(bars[-1][4]); mtm=((close-entry) if direction=="LONG" else (entry-close))/risk
    return {"status":"RESOLVED" if bars else "NO_FUTURE_BARS","first_barrier":first,"t2_before_sl":bool(first_t2 and (not first_sl or first_t2<first_sl)),"eod_mtm_r":round(mtm,6) if mtm is not None else None}

def _barrier_summary(rows):
    a=[r for r in rows if r["decision"]["action"] in {"LONG","SHORT"}]; c=Counter(r["barrier"]["first_barrier"] for r in a if r["barrier"].get("status")=="RESOLVED")
    clean=c["T1_BEFORE_SL"]+c["SL_BEFORE_T1"]; mtm=[float(r["barrier"]["eod_mtm_r"]) for r in a if r["barrier"].get("eod_mtm_r") is not None]
    return {**dict(c),"clean_t1_before_sl_rate_pct":round(100*c["T1_BEFORE_SL"]/clean,4) if clean else None,"t2_before_sl_count":sum(bool(r["barrier"].get("t2_before_sl")) for r in a),"mean_eod_mtm_r":round(statistics.fmean(mtm),6) if mtm else None,"median_eod_mtm_r":round(statistics.median(mtm),6) if mtm else None}

def _input_summary(rows):
    out={}
    for s in STOCKS:
        rr=[r for r in rows if r["symbol"]==s]; ok=sum(r["brain_input_audit"]["all_three_timeframes_core_complete"] for r in rr)
        flags=Counter(); expected=Counter()
        for r in rr:
            for tf in TIMEFRAMES:
                for k,v in r["brain_input_audit"]["timeframes"][tf]["feature_flags"].items():
                    expected[k]+=1; flags[k]+=bool(v)
        out[s]={"observations":len(rr),"core_complete_rate_pct":round(100*ok/len(rr),4) if rr else None,"feature_presence_pct":{k:round(100*flags[k]/expected[k],4) if expected[k] else None for k in ENGINE_KEYS}}
    total=sum(r["brain_input_audit"]["all_three_timeframes_core_complete"] for r in rows)
    return {"candle_only":True,"expected_timeframes":list(TIMEFRAMES),"option_inputs_read":False,"futures_inputs_read":False,"news_inputs_read":False,"core_complete_rate_pct":round(100*total/len(rows),4) if rows else None,"by_stock":out}

async def run_candle_only_four_stock_backtest(provider):
    end=datetime.combine(END_DATE,time(15,30),tzinfo=IST); five={}; errors=[]
    start=datetime.combine(END_DATE-timedelta(days=40),time(9,15),tzinfo=IST)
    for s in STOCKS:
        try: five[s]=await _chunk(provider,s,"5m",start,end)
        except Exception as exc: errors.append({"symbol":s,"timeframe":"5m","error":f"{exc.__class__.__name__}: {str(exc)[:500]}"})
    if errors: return {"protocol_id":PROTOCOL_ID,"status":"SOURCE_CANDLE_DATA_INCOMPLETE","history_errors":errors,"safety":architecture_contract()}
    sessions=common_last_20_sessions(five)
    if len(sessions)<20: return {"protocol_id":PROTOCOL_ID,"status":"INSUFFICIENT_COMMON_TRADING_SESSIONS","sessions":[x.isoformat() for x in sessions],"safety":architecture_contract()}
    earliest=sessions[0]; histories={s:{"5m":five[s]} for s in STOCKS}
    starts={"15m":datetime.combine(earliest-timedelta(days=18),time(9,15),tzinfo=IST),"1h":datetime.combine(earliest-timedelta(days=65),time(9,15),tzinfo=IST)}
    for s in STOCKS:
        for tf in ("15m","1h"):
            try: histories[s][tf]=await _chunk(provider,s,tf,starts[tf],end)
            except Exception as exc: errors.append({"symbol":s,"timeframe":tf,"error":f"{exc.__class__.__name__}: {str(exc)[:500]}"})
    if errors: return {"protocol_id":PROTOCOL_ID,"status":"SOURCE_CANDLE_DATA_INCOMPLETE","history_errors":errors,"safety":architecture_contract()}
    rows=[]; cats=dict(FROZEN_STOCKS)
    for day in sessions:
        for click in deterministic_clicks(day):
            for s in STOCKS:
                technical=core.technical_at(s,histories[s],click); decision=_decision_snapshot(technical)
                rows.append({"trade_date":day.isoformat(),"click_at":click.isoformat(),"symbol":s,"category":cats[s],"decision":decision,"technical":technical,"brain_input_audit":_audit(technical),"outcome":resolve_underlying_path(histories[s]["5m"],click,decision["action"] if decision["action"] in {"LONG","SHORT"} else None),"barrier":_barrier(histories[s]["5m"],click,decision)})
    expected=20*20*4
    return {"protocol_id":PROTOCOL_ID,"status":"COMPLETED" if len(rows)==expected else "OBSERVATION_COUNT_MISMATCH","experiment":{"stocks":[{"symbol":s,"category":c} for s,c in FROZEN_STOCKS],"session_count":len(sessions),"tested_sessions":[x.isoformat() for x in sessions],"clicks_per_day":20,"expected_observations":expected,"observations":len(rows),"latest_completed_session_frozen":END_DATE.isoformat()},"data_coverage":{s:{tf:{"candles":len(histories[s][tf]),"first":str(histories[s][tf][0][0]) if histories[s][tf] else None,"last":str(histories[s][tf][-1][0]) if histories[s][tf] else None} for tf in TIMEFRAMES} for s in STOCKS},"brain_input_audit":_input_summary(rows),"summary":{**_summarize(rows),"barrier_outcomes":_barrier_summary(rows)},"by_stock":{s:{**_summarize([r for r in rows if r["symbol"]==s]),"barrier_outcomes":_barrier_summary([r for r in rows if r["symbol"]==s])} for s in STOCKS},"rows":rows,"methodology":{"completed_candles_only":True,"random_clicks_fixed":True,"current_technical_engine_unchanged":True,"current_mtf_aggregation_unchanged":True,"entry_sl_t1_t2_evaluated":True,"same_bar_sl_t1_ambiguous":True,"no_trade_misses_retained":True},"safety":architecture_contract()}

def architecture_contract():
    return {"version":PROTOCOL_ID,"candle_only":True,"frozen_stocks":[{"symbol":s,"category":c} for s,c in FROZEN_STOCKS],"trading_sessions":20,"random_clicks_per_session":20,"option_data_required":False,"option_chain_read":False,"option_premium_read":False,"option_oi_read":False,"iv_read":False,"greeks_read":False,"futures_read":False,"news_read":False,"live_execution":False,"capital_committed":0,"strategy_policy_changed":False}
