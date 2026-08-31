from __future__ import annotations
import asyncio,csv,io
from datetime import date,datetime,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import httpx

IST=ZoneInfo("Asia/Kolkata")
INSTRUMENT_CSV_URL="https://growwapi-assets.groww.in/instruments/instrument.csv"
PROVIDER="GROWW"

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

async def discover_active_derivatives_universe(now:datetime|None=None):
 today=(now or datetime.now(IST)).date()
 async with httpx.AsyncClient(timeout=45) as client:
  r=await client.get(INSTRUMENT_CSV_URL);r.raise_for_status()
 rows=list(csv.DictReader(io.StringIO(r.text)))
 mcx=[]; nse_futures=[]; option_underlyings=set()
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
  if ex=="MCX" and seg=="COMMODITY" and (it in {"FUT","FUTURE","FUTURES"} or ts.endswith("FUT")):
   mcx.append(base)
  elif ex=="NSE" and seg=="FNO":
   if it in {"FUT","FUTURE","FUTURES"} or ts.endswith("FUT"):nse_futures.append(base)
   elif it in {"CE","PE"} and u:option_underlyings.add(u)
 # nearest active future per underlying to bound request volume
 def nearest(rows):
  best={}
  for x in rows:
   k=x["underlying_symbol"]; cur=best.get(k)
   if cur is None or x["expiry_date"]<cur["expiry_date"]:best[k]=x
  return [best[k] for k in sorted(best)]
 return {"mcx_futures":nearest(mcx),"nse_futures":nearest(nse_futures),
         "fno_option_underlyings":sorted(option_underlyings),
         "counts":{"mcx_futures":len(nearest(mcx)),"nse_futures":len(nearest(nse_futures)),
                   "fno_option_underlyings":len(option_underlyings)}}

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

async def collect_derivatives_universe(provider,store:UniverseStore,shard:int=0,shards:int=4,now:datetime|None=None):
 now=now or datetime.now(IST);await store.initialize();u=await discover_active_derivatives_universe(now)
 contracts=u["mcx_futures"]+u["nse_futures"]
 contracts=[x for i,x in enumerate(contracts) if i%max(1,shards)==shard]
 candle_stats=[];upserted=0
 for contract in contracts:
  latest=await store.latest(contract["trading_symbol"])
  start=max(now-timedelta(days=2),(latest-timedelta(minutes=10)) if latest else now-timedelta(days=2))
  try:
   rows=await _historical_5m(provider,contract,start,now);recs=_records(contract,rows,now);n=await store.upsert_candles(recs);upserted+=n
   candle_stats.append({"symbol":contract["underlying_symbol"],"contract":contract["trading_symbol"],"upserted":n,"ok":True})
  except Exception as exc:candle_stats.append({"symbol":contract["underlying_symbol"],"contract":contract["trading_symbol"],"ok":False,"error":str(exc)[:180]})
 # One option-chain snapshot per underlying, sharded separately. Higher timeframes are derived later from 5m.
 chain_stats=[]
 for i,symbol in enumerate(u["fno_option_underlyings"]):
  if i%max(1,shards)!=shard:continue
  try:
   expiries=await provider.expiries(symbol); expiry=(expiries or [None])[0]
   if not expiry:raise RuntimeError("no expiry")
   chain=await provider.option_chain(symbol,expiry)
   await store.upsert_chain(symbol,str(expiry)[:10],now,chain)
   chain_stats.append({"symbol":symbol,"expiry":str(expiry)[:10],"ok":True})
  except Exception as exc:chain_stats.append({"symbol":symbol,"ok":False,"error":str(exc)[:180]})
 return {"status":"COLLECTED","collected_at":now.isoformat(),"shard":shard,"shards":shards,
  "universe_counts":u["counts"],"contracts_attempted":len(contracts),"candles_upserted":upserted,
  "candle_success":sum(x["ok"] for x in candle_stats),"candle_failed":sum(not x["ok"] for x in candle_stats),
  "option_chains_attempted":len(chain_stats),"option_chain_success":sum(x["ok"] for x in chain_stats),
  "option_chain_failed":sum(not x["ok"] for x in chain_stats)}
