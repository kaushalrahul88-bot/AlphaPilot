from __future__ import annotations
import asyncio,csv,io,time
from datetime import date,datetime,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import httpx

IST=ZoneInfo("Asia/Kolkata")
INSTRUMENT_CSV_URL="https://growwapi-assets.groww.in/instruments/instrument.csv"
PROVIDER="GROWW"
UNIVERSE_CACHE_SECONDS=30*60
_universe_cache={"loaded_at":0.0,"trade_date":None,"value":None}

SCHEMA_SQL="""
CREATE TABLE IF NOT EXISTS universe_candles (
 provider TEXT NOT NULL,
 exchange TEXT NOT NULL,
 segment TEXT NOT NULL,
 underlying_symbol TEXT NOT NULL,
 trading_symbol TEXT NOT NULL,
 groww_symbol TEXT,
 instrument_type TEXT,
 expiry_date DATE,
 timeframe_minutes SMALLINT NOT NULL,
 candle_at TIMESTAMPTZ NOT NULL,
 open NUMERIC NOT NULL, high NUMERIC NOT NULL, low NUMERIC NOT NULL, close NUMERIC NOT NULL,
 volume NUMERIC NOT NULL DEFAULT 0,
 open_interest NUMERIC,
 collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(provider,trading_symbol,timeframe_minutes,candle_at)
);
CREATE INDEX IF NOT EXISTS universe_candles_underlying_time_idx
 ON universe_candles(underlying_symbol,timeframe_minutes,candle_at DESC);

CREATE TABLE IF NOT EXISTS fno_option_chain_snapshots (
 provider TEXT NOT NULL,
 underlying_symbol TEXT NOT NULL,
 expiry_date DATE NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL,
 payload JSONB NOT NULL,
 collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(provider,underlying_symbol,expiry_date,observed_at)
);
"""

UPSERT_CANDLE="""
INSERT INTO universe_candles(provider,exchange,segment,underlying_symbol,trading_symbol,groww_symbol,
 instrument_type,expiry_date,timeframe_minutes,candle_at,open,high,low,close,volume,open_interest,collected_at)
VALUES(%(provider)s,%(exchange)s,%(segment)s,%(underlying_symbol)s,%(trading_symbol)s,%(groww_symbol)s,
 %(instrument_type)s,%(expiry_date)s,%(timeframe_minutes)s,%(candle_at)s,%(open)s,%(high)s,%(low)s,%(close)s,
 %(volume)s,%(open_interest)s,%(collected_at)s)
ON CONFLICT(provider,trading_symbol,timeframe_minutes,candle_at) DO UPDATE SET
 open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,volume=EXCLUDED.volume,
 open_interest=EXCLUDED.open_interest,collected_at=EXCLUDED.collected_at
"""

def _bool(v): return str(v or "").strip().lower() in {"1","true","yes","y"}
def _expiry(v):
 try:return date.fromisoformat(str(v or "")[:10])
 except Exception:return None

def _active_derivatives_from_rows(rows,today:date):
 mcx=[]; nse_futures=[]; option_expiries={}; mcx_option_underlyings=set()
 for row in rows:
  ex=str(row.get("exchange") or "").upper();seg=str(row.get("segment") or "").upper()
  it=str(row.get("instrument_type") or "").upper();exp=_expiry(row.get("expiry_date"))
  if not exp or exp<today:continue
  if row.get("buy_allowed") not in (None,"") and not _bool(row.get("buy_allowed")):continue
  u=str(row.get("underlying_symbol") or row.get("name") or "").upper().strip()
  ts=str(row.get("trading_symbol") or "").upper().strip()
  gs=str(row.get("groww_symbol") or row.get("groww_ticker") or "").strip()
  base={"exchange":ex,"segment":seg,"underlying_symbol":u or ts,"trading_symbol":ts,
        "groww_symbol":gs,"instrument_type":it,"expiry_date":exp.isoformat()}
  if ex=="MCX" and seg=="COMMODITY":
   if it in {"FUT","FUTURE","FUTURES"} or ts.endswith("FUT"):mcx.append(base)
   elif it in {"CE","PE"} and u:mcx_option_underlyings.add(u)
  elif ex=="NSE" and seg=="FNO":
   if it in {"FUT","FUTURE","FUTURES"} or ts.endswith("FUT"):nse_futures.append(base)
   elif it in {"CE","PE"} and u:
    listed=option_expiries.get(u)
    if listed is None or exp<listed:option_expiries[u]=exp
 # nearest active future per underlying to bound request volume
 def nearest(rows):
  best={}
  for x in rows:
   k=x["underlying_symbol"]; cur=best.get(k)
   if cur is None or x["expiry_date"]<cur["expiry_date"]:best[k]=x
  return [best[k] for k in sorted(best)]
 mcx_nearest=nearest(mcx); nse_nearest=nearest(nse_futures)
 mcx_future_symbols={row["underlying_symbol"] for row in mcx_nearest}
 active_mcx_options=sorted(mcx_option_underlyings & mcx_future_symbols)
 return {"mcx_futures":mcx_nearest,"nse_futures":nse_nearest,
         "mcx_option_underlyings":active_mcx_options,
         "fno_option_underlyings":sorted(option_expiries),
         "fno_option_expiries":{symbol:option_expiries[symbol].isoformat() for symbol in sorted(option_expiries)},
         "counts":{"mcx_futures":len(mcx_nearest),"nse_futures":len(nse_nearest),
                   "mcx_option_underlyings":len(active_mcx_options),
                   "fno_option_underlyings":len(option_expiries)}}

async def discover_active_derivatives_universe(now:datetime|None=None,force:bool=False):
 today=(now or datetime.now(IST)).date(); loaded_at=time.monotonic()
 cached=_universe_cache.get("value")
 if (not force and cached and _universe_cache.get("trade_date")==today
     and loaded_at-float(_universe_cache.get("loaded_at") or 0)<UNIVERSE_CACHE_SECONDS):
  return cached
 async with httpx.AsyncClient(timeout=45) as client:
  r=await client.get(INSTRUMENT_CSV_URL);r.raise_for_status()
 rows=list(csv.DictReader(io.StringIO(r.text)))
 value=_active_derivatives_from_rows(rows,today)
 _universe_cache.update({"loaded_at":loaded_at,"trade_date":today,"value":value})
 return value

class UniverseStore:
 def __init__(self,database_url):self.database_url=str(database_url or "").strip()
 def _connect(self):
  import psycopg
  return psycopg.connect(self.database_url,connect_timeout=10)
 async def initialize(self):
  def run():
   with self._connect() as c:
    with c.cursor() as cur:cur.execute(SCHEMA_SQL)
  await asyncio.to_thread(run)
 async def latest(self,trading_symbol):
  def run():
   with self._connect() as c:
    with c.cursor() as cur:
     cur.execute("SELECT MAX(candle_at) FROM universe_candles WHERE provider=%s AND trading_symbol=%s AND timeframe_minutes=5",(PROVIDER,trading_symbol))
     x=cur.fetchone();return x[0] if x else None
  return await asyncio.to_thread(run)
 async def upsert_candles(self,records):
  if not records:return 0
  def run():
   with self._connect() as c:
    with c.cursor() as cur:cur.executemany(UPSERT_CANDLE,records)
   return len(records)
  return await asyncio.to_thread(run)
 async def upsert_chain(self,underlying,expiry,observed,payload):
  import json
  def run():
   with self._connect() as c:
    with c.cursor() as cur:
     cur.execute("""INSERT INTO fno_option_chain_snapshots(provider,underlying_symbol,expiry_date,observed_at,payload,collected_at)
      VALUES(%s,%s,%s,%s,%s::jsonb,NOW()) ON CONFLICT DO NOTHING""",(PROVIDER,underlying,expiry,observed,json.dumps(payload)))
  await asyncio.to_thread(run)

async def _historical_5m(provider,contract,start,end):
 throttle=getattr(provider,"_throttle",None)
 if callable(throttle):await throttle()
 params={"exchange":contract["exchange"],"segment":contract["segment"],"groww_symbol":contract["groww_symbol"],
         "start_time":start.strftime("%Y-%m-%d %H:%M:%S"),"end_time":end.strftime("%Y-%m-%d %H:%M:%S"),"candle_interval":"5minute"}
 async with httpx.AsyncClient(timeout=40) as client:
  r=await client.get(f"{provider.BASE_URL}/v1/historical/candles",headers=await provider._headers(),params=params)
 r.raise_for_status();body=r.json();p=body.get("payload",body) if isinstance(body,dict) else {}
 return p.get("candles",[]) if isinstance(p,dict) else []

def _records(contract,rows,collected_at):
 out=[]
 for row in rows or []:
  if not isinstance(row,(list,tuple)) or len(row)<5:continue
  try:
   ts=datetime.fromisoformat(str(row[0]).replace("Z","+00:00"))
   if ts.tzinfo is None:ts=ts.replace(tzinfo=IST)
   o,h,l,cl=[Decimal(str(x)) for x in row[1:5]]
   if min(o,h,l,cl)<=0 or h<l:continue
  except Exception:continue
  out.append({"provider":PROVIDER,"exchange":contract["exchange"],"segment":contract["segment"],
   "underlying_symbol":contract["underlying_symbol"],"trading_symbol":contract["trading_symbol"],
   "groww_symbol":contract["groww_symbol"],"instrument_type":contract["instrument_type"],
   "expiry_date":contract["expiry_date"],"timeframe_minutes":5,"candle_at":ts,
   "open":o,"high":h,"low":l,"close":cl,"volume":Decimal(str(row[5] if len(row)>5 and row[5] is not None else 0)),
   "open_interest":Decimal(str(row[6])) if len(row)>6 and row[6] is not None else None,"collected_at":collected_at})
 return out

async def collect_derivatives_universe(provider,store:UniverseStore,shard:int=0,shards:int=4,now:datetime|None=None,
                                      offset:int=0,limit:int=4):
 now=now or datetime.now(IST);await store.initialize();u=await discover_active_derivatives_universe(now)
 symbols=[s for i,s in enumerate(u["fno_option_underlyings"]) if i%max(1,shards)==shard]
 offset=max(0,int(offset));limit=max(1,min(int(limit),8));batch=symbols[offset:offset+limit]
 chain_stats=[]
 for symbol in batch:
  try:
   expiry=(u.get("fno_option_expiries") or {}).get(symbol)
   expiry_source="GROWW_INSTRUMENT_MASTER"
   if not expiry:
    expiries=await provider.expiries(symbol); expiry=(expiries or [None])[0]
    expiry_source="GROWW_EXPIRIES_API"
   if not expiry:raise RuntimeError("no expiry")
   chain=await provider.option_chain(symbol,expiry)
   await store.upsert_chain(symbol,str(expiry)[:10],now,chain)
   chain_stats.append({"symbol":symbol,"expiry":str(expiry)[:10],"expiry_source":expiry_source,"ok":True})
  except Exception as exc:
   chain_stats.append({"symbol":symbol,"ok":False,"error":str(exc)[:180]})
 successes=sum(x["ok"] for x in chain_stats); failures=len(chain_stats)-successes
 status="COLLECTED" if failures==0 else "PARTIAL" if successes else "FAILED"
 return {"status":status,"collected_at":now.isoformat(),"shard":shard,"shards":shards,
  "universe_counts":u["counts"],"shard_underlyings":len(symbols),"offset":offset,"limit":limit,
  "batch_size":len(batch),"next_offset":offset+len(batch),"done":offset+len(batch)>=len(symbols),
  "historical_candles_persisted":False,
  "historical_candle_policy":"FETCH_FROM_GROWW_ON_DEMAND",
  "live_ephemeral_policy":"PERSIST_POINT_IN_TIME_STATE_NOT_RELIABLY_RECONSTRUCTIBLE",
  "option_chains_attempted":len(chain_stats),"option_chain_success":successes,
  "option_chain_failed":failures,"results":chain_stats}
