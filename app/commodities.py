import csv
import os
import tempfile
import time
from datetime import date, datetime, timedelta, time as dt_time
from statistics import mean
from zoneinfo import ZoneInfo

import httpx

INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
SUPPORTED_COMMODITIES = {"CRUDEOIL", "NATURALGAS"}
_CACHE_TTL_SECONDS = 6 * 60 * 60
_contract_cache = {}


def _as_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _parse_expiry(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _row_matches_symbol(row, symbol, today):
    if str(row.get("exchange") or "").upper() != "MCX": return None
    if str(row.get("segment") or "").upper() != "COMMODITY": return None
    underlying = str(row.get("underlying_symbol") or row.get("name") or "").upper().replace(" ", "")
    trading_symbol = str(row.get("trading_symbol") or "").upper()
    instrument_type = str(row.get("instrument_type") or "").upper()
    if underlying != symbol and not trading_symbol.startswith(symbol): return None
    if instrument_type not in {"FUT", "FUTURE", "FUTURES"} and not trading_symbol.endswith("FUT"): return None
    expiry = _parse_expiry(row.get("expiry_date"))
    if not expiry or expiry < today: return None
    if row.get("buy_allowed") not in (None, "") and not _as_bool(row.get("buy_allowed")): return None
    return expiry


def _response_error(response):
    body = response.text.strip()
    if len(body) > 500: body = body[:500] + "…"
    return f"HTTP {response.status_code}: {body or response.reason_phrase}"


def _validate_quote_response(data):
    if not isinstance(data, dict):
        raise RuntimeError("Groww quote response is not a JSON object")
    status = str(data.get("status") or "").upper()
    if status and status != "SUCCESS":
        raise RuntimeError(f"Groww quote status is {status}")
    payload = data.get("payload", data)
    if not isinstance(payload, dict):
        raise RuntimeError("Groww quote payload is missing")
    try:
        last_price = float(payload.get("last_price"))
    except (TypeError, ValueError):
        raise RuntimeError("Groww quote payload has no valid last_price")
    if last_price <= 0:
        raise RuntimeError("Groww quote last_price must be positive")
    return payload, last_price


async def _download_instrument_master_to_tempfile():
    fd, path = tempfile.mkstemp(prefix="alphapilot-groww-", suffix=".csv")
    os.close(fd)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=20.0)) as client:
            async with client.stream("GET", INSTRUMENT_CSV_URL) as response:
                response.raise_for_status()
                with open(path, "wb") as output:
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024): output.write(chunk)
        return path
    except Exception:
        try: os.remove(path)
        except OSError: pass
        raise


async def resolve_nearest_mcx_future(symbol, force=False):
    symbol = str(symbol or "").strip().upper()
    if symbol not in SUPPORTED_COMMODITIES:
        raise ValueError(f"Unsupported commodity {symbol}. Supported: {', '.join(sorted(SUPPORTED_COMMODITIES))}")
    now_ts = time.time(); cached = _contract_cache.get(symbol)
    if not force and cached and now_ts - cached["loaded_at"] < _CACHE_TTL_SECONDS: return dict(cached["contract"])
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date(); path = await _download_instrument_master_to_tempfile(); best = None
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                expiry = _row_matches_symbol(row, symbol, today)
                if not expiry: continue
                if best is None or expiry < best[0]:
                    best = (expiry, {"underlying":symbol,"exchange":"MCX","segment":"COMMODITY","trading_symbol":str(row.get("trading_symbol") or ""),"groww_symbol":str(row.get("groww_symbol") or ""),"expiry_date":expiry.isoformat(),"lot_size":int(float(row.get("lot_size") or 0)) if str(row.get("lot_size") or "").strip() else None,"tick_size":float(row.get("tick_size") or 0) if str(row.get("tick_size") or "").strip() else None,"instrument_type":str(row.get("instrument_type") or "FUT")})
    finally:
        try: os.remove(path)
        except OSError: pass
    if best is None: raise RuntimeError(f"No active MCX future found for {symbol}")
    contract = best[1]; _contract_cache[symbol] = {"loaded_at":now_ts,"contract":contract}; return dict(contract)


async def commodity_quote(provider, symbol, contract=None):
    contract = contract or await resolve_nearest_mcx_future(symbol)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{provider.BASE_URL}/v1/live-data/quote",headers=await provider._headers(),params={"exchange":contract["exchange"],"segment":contract["segment"],"trading_symbol":contract["trading_symbol"]})
    response.raise_for_status()
    data = response.json()
    payload, last_price = _validate_quote_response(data)
    return {"provider":"GROWW","contract":contract,"data":data,"validation":{"status":"PASS","last_price":last_price,"payload_keys":sorted(payload.keys())}}


async def commodity_candles(provider, symbol, timeframe="5m", contract=None):
    contract = contract or await resolve_nearest_mcx_future(symbol)
    interval_map = {"5m":("5minute",5,7),"15m":("15minute",15,14),"1h":("1hour",60,60)}
    candle_interval, legacy_minutes, days = interval_map.get(timeframe,("5minute",5,7))
    now = datetime.now(ZoneInfo("Asia/Kolkata")); start = now - timedelta(days=days)
    start_text=start.strftime("%Y-%m-%d %H:%M:%S"); end_text=now.strftime("%Y-%m-%d %H:%M:%S"); headers=await provider._headers(); attempts=[]
    modern_params={"exchange":contract["exchange"],"segment":contract["segment"],"groww_symbol":contract["groww_symbol"],"start_time":start_text,"end_time":end_text,"candle_interval":candle_interval}
    async with httpx.AsyncClient(timeout=30) as client: modern=await client.get(f"{provider.BASE_URL}/v1/historical/candles",headers=headers,params=modern_params)
    if modern.status_code==200:
        data=modern.json(); payload=data.get("payload",data); candles=payload.get("candles",[]) if isinstance(payload,dict) else []
        if candles: return {"provider":"GROWW","contract":contract,"timeframe":timeframe,"candles":candles,"historical_source":"backtesting"}
        attempts.append({"endpoint":"/v1/historical/candles","error":"200 response but no candles returned"})
    else: attempts.append({"endpoint":"/v1/historical/candles","error":_response_error(modern)})
    legacy_params={"exchange":contract["exchange"],"segment":contract["segment"],"trading_symbol":contract["trading_symbol"],"start_time":start_text,"end_time":end_text,"interval_in_minutes":str(legacy_minutes)}
    async with httpx.AsyncClient(timeout=30) as client: legacy=await client.get(f"{provider.BASE_URL}/v1/historical/candle/range",headers=headers,params=legacy_params)
    if legacy.status_code==200:
        data=legacy.json(); payload=data.get("payload",data); candles=payload.get("candles",[]) if isinstance(payload,dict) else []
        if candles: return {"provider":"GROWW","contract":contract,"timeframe":timeframe,"candles":candles,"historical_source":"legacy-range","attempts":attempts}
        attempts.append({"endpoint":"/v1/historical/candle/range","error":"200 response but no candles returned"})
    else: attempts.append({"endpoint":"/v1/historical/candle/range","error":_response_error(legacy)})
    raise RuntimeError(f"Groww returned no MCX historical candles. Attempts: {attempts}")


def _f(v, default=0.0):
    try: return default if v is None else float(v)
    except (TypeError, ValueError): return default


def _clean_mcx_candles(candles):
    out=[]
    for c in candles:
        if not isinstance(c,(list,tuple)) or len(c)<5: continue
        ts,o,h,l,cl=c[:5]; vol=c[5] if len(c)>5 else 0
        o,h,l,cl,vol=_f(o),_f(h),_f(l),_f(cl),max(0,_f(vol))
        if min(o,h,l,cl)<=0 or h<l: continue
        out.append([ts,o,h,l,cl,vol])
    return out


def _ema(values, period):
    if not values: return 0
    k=2/(period+1); value=values[0]
    for x in values[1:]: value=x*k+value*(1-k)
    return value


def _rsi(values, period=14):
    if len(values)<=period: return 50
    gains=[]; losses=[]
    for i in range(1,len(values)):
        diff=values[i]-values[i-1]; gains.append(max(diff,0)); losses.append(max(-diff,0))
    ag=mean(gains[-period:]); al=mean(losses[-period:])
    return 100 if al==0 else 100-100/(1+ag/al)


def _atr(candles, period=14):
    if len(candles)<2: return 0
    values=[]
    for i in range(1,len(candles)):
        h,l,pc=candles[i][2],candles[i][3],candles[i-1][4]; values.append(max(h-l,abs(h-pc),abs(l-pc)))
    return mean(values[-period:]) if values else 0


def _mcx_structure(candles, n=20):
    rows=candles[-n:]
    if len(rows)<6: return "RANGE",min(x[3] for x in rows),max(x[2] for x in rows)
    half=len(rows)//2; a=rows[:half]; b=rows[half:]; ah=max(x[2] for x in a); al=min(x[3] for x in a); bh=max(x[2] for x in b); bl=min(x[3] for x in b)
    state="UPTREND" if bh>ah and bl>al else "DOWNTREND" if bh<ah and bl<al else "RANGE"
    return state,min(x[3] for x in rows),max(x[2] for x in rows)


def analyze_commodity_candles(symbol, candles, min_rr=1.5):
    c=_clean_mcx_candles(candles)
    if len(c)<60: return {"symbol":symbol,"status":"NO_TRADE","signal":"NO TRADE","reason":"Not enough MCX history","clean_candles":len(c)}
    closes=[x[4] for x in c]; last=closes[-1]; e9=_ema(closes,9); e20=_ema(closes,20); e50=_ema(closes,50); r=_rsi(closes); a=_atr(c); st,sup,res=_mcx_structure(c)
    trend=0
    if last>e20>e50: trend+=2
    elif last<e20<e50: trend-=2
    trend += 1 if e9>e20 else -1
    momentum = 1 if r>=55 else -1 if r<=45 else 0
    structure_score = 2 if st=="UPTREND" else -2 if st=="DOWNTREND" else 0
    roc=((last/closes[-11])-1)*100 if len(closes)>10 else 0
    momentum += 1 if roc>.2 else -1 if roc<-.2 else 0
    bias=trend+momentum+structure_score
    alpha=max(0,min(100,50+bias*8))
    direction="BUY" if bias>=3 and r<78 else "SELL" if bias<=-3 and r>22 else "NO TRADE"
    base={"symbol":symbol,"alpha_score":round(alpha,1),"signal":direction,"price":round(last,2),"latest_candle_at":str(c[-1][0]),"ema9":round(e9,2),"ema20":round(e20,2),"ema50":round(e50,2),"rsi14":round(r,2),"atr14":round(a,2),"market_structure":st,"recent_support":round(sup,2),"recent_resistance":round(res,2),"clean_candles":len(c)}
    if direction=="NO TRADE": return {**base,"status":"NO_TRADE","reason":"Commodity confluence threshold not met"}
    risk=max(a*1.25,last*.003)
    if direction=="BUY": stop=min(last-risk,sup-.15*a) if sup<last else last-risk; rrisk=last-stop; t1=last+rrisk*min_rr; t2=last+rrisk*max(2,min_rr+.5)
    else: stop=max(last+risk,res+.15*a) if res>last else last+risk; rrisk=stop-last; t1=last-rrisk*min_rr; t2=last-rrisk*max(2,min_rr+.5)
    return {**base,"status":"SETUP","entry":round(last,2),"stop_loss":round(stop,2),"target1":round(t1,2),"target2":round(t2,2),"risk_reward":round(min_rr,2)}


def mcx_session_status(now=None):
    now=now or datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday()>=5: return {"status":"CLOSED","is_open":False,"checked_at":now.isoformat()}
    current=now.time(); is_open=dt_time(9,0)<=current<=dt_time(23,30)
    return {"status":"OPEN" if is_open else "CLOSED","is_open":is_open,"checked_at":now.isoformat()}


def _fresh_enough(timestamp, timeframe, now):
    try:
        ts=datetime.fromisoformat(str(timestamp))
        if ts.tzinfo is None: ts=ts.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        age=(now-ts.astimezone(ZoneInfo("Asia/Kolkata"))).total_seconds()/60
        limit={"5m":15,"15m":35,"1h":90}.get(timeframe,35)
        return age<=limit,round(age,1)
    except Exception: return False,None


async def commodity_mtf_scan(provider, symbol, min_rr=1.5):
    symbol=str(symbol or "").upper(); contract=await resolve_nearest_mcx_future(symbol); now=datetime.now(ZoneInfo("Asia/Kolkata")); session=mcx_session_status(now); frames={}; directions=[]
    for tf in ("5m","15m","1h"):
        result=await commodity_candles(provider,symbol,tf,contract); analysis=analyze_commodity_candles(symbol,result.get("candles",[]),min_rr); fresh,age=_fresh_enough(analysis.get("latest_candle_at"),tf,now); analysis["fresh"]=fresh; analysis["age_minutes"]=age; analysis["historical_source"]=result.get("historical_source"); frames[tf]=analysis
        if analysis.get("signal") in {"BUY","SELL"}: directions.append(analysis["signal"])
    buy_count=directions.count("BUY"); sell_count=directions.count("SELL"); action="BUY" if buy_count>=2 else "SELL" if sell_count>=2 else "NO TRADE"
    raw_alpha=round(mean([_f(frames[tf].get("alpha_score"),50) for tf in frames]),1)
    directional_strength=round(100-raw_alpha if action=="SELL" else raw_alpha,1)
    fresh_all=all(bool(frames[tf].get("fresh")) for tf in frames)
    executable=session["is_open"] and fresh_all and action!="NO TRADE" and directional_strength>=65
    reference=next((frames[tf] for tf in ("15m","5m","1h") if frames[tf].get("signal")==action and frames[tf].get("status")=="SETUP"),None)
    return {"provider":"GROWW","mode":"MCX_COMMODITY_MTF","symbol":symbol,"contract":contract,"market_session":session,"timeframes":frames,"action":action,"alpha_score":directional_strength,"raw_alpha_score":raw_alpha,"fresh_market_data":fresh_all if session["is_open"] else None,"execution_ready":bool(executable),"status":"READY" if executable else "SNAPSHOT" if not session["is_open"] else "WATCH","entry":reference.get("entry") if reference else None,"stop_loss":reference.get("stop_loss") if reference else None,"target1":reference.get("target1") if reference else None,"target2":reference.get("target2") if reference else None,"risk_reward":reference.get("risk_reward") if reference else None,"blockers":(["MCX market is closed; snapshot only."] if not session["is_open"] else [])+(["Fresh 5m/15m/1h commodity candles are required."] if session["is_open"] and not fresh_all else [])+(["At least 2 of 3 timeframes must agree on BUY or SELL."] if action=="NO TRADE" else [])+(["Average directional strength must be at least 65."] if action!="NO TRADE" and directional_strength<65 else [])}


async def commodity_probe(provider, symbol):
    contract=await resolve_nearest_mcx_future(symbol); quote_result=None; candle_result=None; errors=[]
    try: quote_result=await commodity_quote(provider,symbol,contract)
    except Exception as exc: errors.append({"check":"quote","error":str(exc)})
    try: candle_result=await commodity_candles(provider,symbol,"5m",contract)
    except Exception as exc: errors.append({"check":"candles","error":str(exc)})
    candle_count=len(candle_result.get("candles",[])) if candle_result else 0
    quote_ok=quote_result is not None and quote_result.get("validation",{}).get("status")=="PASS"
    candles_ok=candle_result is not None and candle_count>0
    return {"symbol":symbol.upper(),"contract":contract,"quote_ok":quote_ok,"candles_ok":candles_ok,"candle_count":candle_count,"historical_source":candle_result.get("historical_source") if candle_result else None,"quote":quote_result,"errors":errors,"ready_for_phase1":quote_ok and candles_ok}
