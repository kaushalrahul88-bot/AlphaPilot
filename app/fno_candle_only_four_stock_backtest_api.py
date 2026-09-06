"""Durable internal API for the candle-only four-stock replay."""
from __future__ import annotations
import asyncio, os, traceback, uuid
from datetime import datetime, timezone
from fastapi import Header, HTTPException
from psycopg.types.json import Jsonb
from .fno_candle_only_four_stock_backtest_v1 import PROTOCOL_ID, run_candle_only_four_stock_backtest
from .providers.factory import get_provider

UTC=timezone.utc
RUN_TABLE="fno_candle_only_four_stock_backtest_runs_v1"
SQL=f"""CREATE TABLE IF NOT EXISTS {RUN_TABLE}(
run_id TEXT PRIMARY KEY,protocol_id TEXT NOT NULL,deployment_commit TEXT,status TEXT NOT NULL CHECK(status IN('RUNNING','COMPLETED','FAILED')),
started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),completed_at TIMESTAMPTZ,heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
attempt_count INTEGER NOT NULL DEFAULT 0,result_json JSONB,error TEXT,traceback TEXT);
CREATE INDEX IF NOT EXISTS fno_candle_only_four_stock_runs_started_idx ON {RUN_TABLE}(started_at DESC);"""
_task=None; _task_run_id=None; _lock=None

def _connect(url):
    import psycopg
    return psycopg.connect(url,connect_timeout=10)
def _ensure_sync(url):
    with _connect(url) as c:
        with c.cursor() as cur: cur.execute(SQL)
        c.commit()
async def _ensure(url): await asyncio.to_thread(_ensure_sync,url)
def _row(row):
    if not row:return None
    keys=("run_id","protocol_id","deployment_commit","status","started_at","updated_at","completed_at","heartbeat_at","attempt_count","result_json","error","traceback")
    d=dict(zip(keys,row))
    for k in ("started_at","updated_at","completed_at","heartbeat_at"):
        if isinstance(d.get(k),datetime): d[k]=d[k].astimezone(UTC).isoformat()
    return d
def _latest_sync(url):
    with _connect(url) as c:
        with c.cursor() as cur:
            cur.execute(f"SELECT run_id,protocol_id,deployment_commit,status,started_at,updated_at,completed_at,heartbeat_at,attempt_count,result_json,error,traceback FROM {RUN_TABLE} WHERE protocol_id=%s ORDER BY started_at DESC LIMIT 1",(PROTOCOL_ID,))
            return _row(cur.fetchone())
async def _latest(url): return await asyncio.to_thread(_latest_sync,url)
def _create_sync(url,run_id,commit):
    with _connect(url) as c:
        with c.cursor() as cur: cur.execute(f"INSERT INTO {RUN_TABLE}(run_id,protocol_id,deployment_commit,status) VALUES(%s,%s,%s,'RUNNING')",(run_id,PROTOCOL_ID,commit or None))
        c.commit()
async def _create(url,run_id,commit): await asyncio.to_thread(_create_sync,url,run_id,commit)
def _attempt_sync(url,run_id):
    with _connect(url) as c:
        with c.cursor() as cur: cur.execute(f"UPDATE {RUN_TABLE} SET attempt_count=attempt_count+1,updated_at=NOW(),heartbeat_at=NOW() WHERE run_id=%s AND status='RUNNING'",(run_id,))
        c.commit()
async def _attempt(url,run_id): await asyncio.to_thread(_attempt_sync,url,run_id)
def _complete_sync(url,run_id,result):
    with _connect(url) as c:
        with c.cursor() as cur: cur.execute(f"UPDATE {RUN_TABLE} SET status='COMPLETED',result_json=%s,error=NULL,traceback=NULL,updated_at=NOW(),heartbeat_at=NOW(),completed_at=NOW() WHERE run_id=%s AND status='RUNNING'",(Jsonb(dict(result)),run_id))
        c.commit()
async def _complete(url,run_id,result): await asyncio.to_thread(_complete_sync,url,run_id,result)
def _fail_sync(url,run_id,error,trace):
    with _connect(url) as c:
        with c.cursor() as cur: cur.execute(f"UPDATE {RUN_TABLE} SET status='FAILED',error=%s,traceback=%s,updated_at=NOW(),heartbeat_at=NOW(),completed_at=NOW() WHERE run_id=%s AND status='RUNNING'",(error,trace,run_id))
        c.commit()
async def _fail(url,run_id,error,trace): await asyncio.to_thread(_fail_sync,url,run_id,error,trace)

def _summary(run):
    if not run:return {"status":"IDLE","protocol_id":PROTOCOL_ID,"candle_only":True,"option_data_required":False,"live_execution":False,"capital_committed":0}
    d={"run_id":run.get("run_id"),"protocol_id":run.get("protocol_id"),"deployment_commit":run.get("deployment_commit"),"status":run.get("status"),"attempt_count":int(run.get("attempt_count") or 0),"heartbeat_at":run.get("heartbeat_at"),"error":run.get("error") if run.get("status")=="FAILED" else None,"candle_only":True,"option_data_required":False,"live_execution":False,"capital_committed":0}
    if run.get("status")=="COMPLETED":
        r=run.get("result_json") or {}; d.update({"experiment":r.get("experiment"),"summary":r.get("summary"),"brain_input_audit":r.get("brain_input_audit"),"safety":r.get("safety")})
    return d

async def _run(settings,run_id):
    global _task,_task_run_id
    try:
        await _attempt(settings.database_url,run_id)
        result=await run_candle_only_four_stock_backtest(get_provider(settings))
        if result.get("status")!="COMPLETED": raise RuntimeError(f"replay did not complete: {result.get('status')} {result.get('history_errors') or ''}")
        await _complete(settings.database_url,run_id,result)
    except asyncio.CancelledError: raise
    except Exception as exc: await _fail(settings.database_url,run_id,f"{exc.__class__.__name__}: {str(exc)[:1600]}",traceback.format_exc()[-16000:])
    finally:
        if _task_run_id==run_id:_task=None;_task_run_id=None

def _active(run_id=None): return bool(_task is not None and not _task.done() and (run_id is None or _task_run_id==run_id))
async def _worker(settings,run):
    global _task,_task_run_id,_lock
    if not run or run.get("status")!="RUNNING":return
    run_id=str(run.get("run_id") or "")
    if not run_id or _active(run_id):return
    if _lock is None:_lock=asyncio.Lock()
    async with _lock:
        if _active(run_id):return
        _task_run_id=run_id;_task=asyncio.create_task(_run(settings,run_id))

def register_fno_candle_only_four_stock_backtest_routes(app,settings,collector_auth):
    if getattr(app.state,"fno_candle_only_four_stock_backtest_registered",False):return
    app.state.fno_candle_only_four_stock_backtest_registered=True
    @app.post("/v1/internal/fno/candle-only-four-stock-backtest/start")
    async def start(x_collector_token:str|None=Header(default=None)):
        collector_auth(x_collector_token);await _ensure(settings.database_url);latest=await _latest(settings.database_url)
        if latest and latest.get("status")=="RUNNING":await _worker(settings,latest);return _summary(latest)
        run_id=uuid.uuid4().hex;await _create(settings.database_url,run_id,os.getenv("RENDER_GIT_COMMIT",""));created=await _latest(settings.database_url);await _worker(settings,created);return _summary(created)
    @app.get("/v1/internal/fno/candle-only-four-stock-backtest/status")
    async def status(x_collector_token:str|None=Header(default=None)):
        collector_auth(x_collector_token);await _ensure(settings.database_url);run=await _latest(settings.database_url);await _worker(settings,run);return _summary(await _latest(settings.database_url))
    @app.get("/v1/internal/fno/candle-only-four-stock-backtest/result")
    async def result(x_collector_token:str|None=Header(default=None)):
        collector_auth(x_collector_token);await _ensure(settings.database_url);run=await _latest(settings.database_url);await _worker(settings,run)
        if not run:raise HTTPException(409,detail={"status":"IDLE"})
        if run.get("status")=="FAILED":raise HTTPException(500,detail={"run_id":run.get("run_id"),"error":run.get("error"),"traceback":run.get("traceback")})
        if run.get("status")!="COMPLETED":raise HTTPException(409,detail={"run_id":run.get("run_id"),"status":run.get("status")})
        return run.get("result_json") or {}

def architecture_contract():
    return {"version":"FNO_CANDLE_ONLY_FOUR_STOCK_BACKTEST_API_V1","collector_auth_required":True,"durable_run_state":True,"restartable":True,"candle_only":True,"option_data_required":False,"live_execution":False,"capital_committed":0}
