from __future__ import annotations
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from .commodity_backtest import _fetch_chunked, _ts
from .commodities import resolve_nearest_mcx_future
from .copper_contract_sync_audit import _fetch_legacy_underlying_day
from .copper_market_brain_direction_audit import REFERENCE_CONTRACT

IST=ZoneInfo("Asia/Kolkata")
DAYS=(date(2026,8,10),date(2026,8,17),date(2026,8,24))

def _norm(row):
    if not row:return None
    return {
      "timestamp":_ts(row[0]).replace(second=0,microsecond=0).isoformat(),
      "open":float(row[1]),"high":float(row[2]),"low":float(row[3]),"close":float(row[4]),
      "volume":float(row[5] or 0) if len(row)>5 else 0.0,
      "oi":None if len(row)<=6 or row[6] is None else float(row[6]),
    }

def _at(rows,day,hour=9,minute=0):
    target=datetime.combine(day,time(hour,minute),tzinfo=IST)
    for r in rows or []:
        try:
            if _ts(r[0]).replace(second=0,microsecond=0)==target:return r
        except Exception:continue
    return None

def _same(a,b,fields):
    if not a or not b:return False
    return all(a.get(k)==b.get(k) for k in fields)

async def run_provider_discrepancy_triangulation(provider,store):
    contract=await resolve_nearest_mcx_future("COPPER",force=True)
    if str(contract.get("trading_symbol") or "").upper()!=REFERENCE_CONTRACT:
        raise RuntimeError(f"Expected {REFERENCE_CONTRACT}, got {contract.get('trading_symbol')}")
    await store.initialize()
    out=[]
    for day in DAYS:
        start=datetime.combine(day,time(9,0),tzinfo=IST)
        end=datetime.combine(day,time(23,30),tzinfo=IST)
        modern=await _fetch_chunked(provider,contract,5,start,end)
        legacy=await _fetch_legacy_underlying_day(provider,contract,day)
        segs=await store.read_symbol_contract_segments("COPPER",5,start,end)
        stored=[]
        for s in segs:
            if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT:
                stored.extend(s.get("candles") or [])
        m=_norm(_at(modern,day)); l=_norm(_at(legacy,day)); s=_norm(_at(stored,day))
        fields=("open","high","low","close","volume")
        out.append({
          "date":day.isoformat(),"timestamp":"09:00",
          "modern":m,"legacy":l,"stored":s,
          "presence":{"modern":m is not None,"legacy":l is not None,"stored":s is not None},
          "matches":{
            "stored_modern_ohlcv":_same(s,m,fields),
            "stored_legacy_ohlcv":_same(s,l,fields),
            "modern_legacy_ohlcv":_same(m,l,fields),
            "stored_modern_ohlc":_same(s,m,("open","high","low","close")),
            "stored_legacy_ohlc":_same(s,l,("open","high","low","close")),
          },
          "row_counts":{"modern":len(modern),"legacy":len(legacy),"stored":len(stored)},
        })
    return {
      "mode":"COPPER_PROVIDER_DISCREPANCY_TRIANGULATION_V1",
      "reference_contract":REFERENCE_CONTRACT,
      "resolved_expiry":contract.get("expiry_date"),
      "days":out,
      "interpretation_rule":"No source is declared authoritative merely because it matches storage; disagreements must be resolved before certifying the replay dataset.",
      "read_only":True,
    }
