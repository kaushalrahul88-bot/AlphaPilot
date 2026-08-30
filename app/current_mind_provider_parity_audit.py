from __future__ import annotations
from hashlib import sha256
import json
from .commodity_backtest import _fetch_chunked, _ts
from .copper_market_brain_direction_audit import PRIMARY_END, PRIMARY_START, REFERENCE_CONTRACT
from .commodities import resolve_nearest_mcx_future

def _norm(row):
    return [str(_ts(row[0]).replace(second=0,microsecond=0).isoformat()),
            float(row[1]),float(row[2]),float(row[3]),float(row[4]),
            float(row[5] or 0) if len(row)>5 else 0.0,
            None if len(row)<=6 or row[6] is None else float(row[6])]

def _map(rows):
    out={}
    for r in rows or []:
        if not isinstance(r,(list,tuple)) or len(r)<5: continue
        try: n=_norm(r)
        except Exception: continue
        out[n[0]]=n
    return out

def _digest(rows, fields=5):
    payload=[[r[i] for i in range(min(fields+1,len(r)))] for r in sorted(rows,key=lambda x:x[0])]
    return sha256(json.dumps(payload,separators=(",",":"),sort_keys=False).encode()).hexdigest()

async def run_provider_parity_audit(provider, store):
    contract=await resolve_nearest_mcx_future("COPPER",force=True)
    if str(contract.get("trading_symbol") or "").upper()!=REFERENCE_CONTRACT:
        raise RuntimeError(f"Expected {REFERENCE_CONTRACT}, got {contract.get('trading_symbol')}")
    provider_rows=await _fetch_chunked(provider,contract,5,PRIMARY_START,PRIMARY_END)
    provider_rows=[r for r in provider_rows if PRIMARY_START<=_ts(r[0])<=PRIMARY_END]
    await store.initialize()
    segs=await store.read_symbol_contract_segments("COPPER",5,PRIMARY_START,PRIMARY_END)
    target=next((s for s in segs if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT),None)
    if not target: raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    stored_rows=target.get("candles") or []

    p=_map(provider_rows); s=_map(stored_rows)
    pkeys=set(p); skeys=set(s); shared=sorted(pkeys&skeys)
    only_p=sorted(pkeys-skeys); only_s=sorted(skeys-pkeys)
    ohlc_mismatch=[]; volume_mismatch=[]; oi_mismatch=[]
    maxdiff={"open":0.0,"high":0.0,"low":0.0,"close":0.0}
    for ts in shared:
        a,b=p[ts],s[ts]
        diffs=[abs(a[i]-b[i]) for i in range(1,5)]
        for k,d in zip(("open","high","low","close"),diffs): maxdiff[k]=max(maxdiff[k],d)
        if any(d>1e-9 for d in diffs):
            ohlc_mismatch.append({"timestamp":ts,"provider":a[1:5],"stored":b[1:5]})
        if abs(a[5]-b[5])>1e-9:
            volume_mismatch.append({"timestamp":ts,"provider":a[5],"stored":b[5]})
        if a[6] is not None and b[6] is not None and abs(a[6]-b[6])>1e-9:
            oi_mismatch.append({"timestamp":ts,"provider":a[6],"stored":b[6]})
    return {
      "mode":"COPPER_CURRENT_MIND_PROVIDER_PARITY_AUDIT_V1",
      "provider":"GROWW","reference_contract":REFERENCE_CONTRACT,
      "resolved_expiry":contract.get("expiry_date"),
      "window":{"start":PRIMARY_START.isoformat(),"end":PRIMARY_END.isoformat()},
      "provider_rows":len(p),"stored_rows":len(s),"shared_rows":len(shared),
      "provider_only_timestamps":len(only_p),"stored_only_timestamps":len(only_s),
      "provider_only_sample":only_p[:50],"stored_only_sample":only_s[:50],
      "ohlc_mismatch_count":len(ohlc_mismatch),"volume_mismatch_count":len(volume_mismatch),
      "oi_mismatch_count_when_both_present":len(oi_mismatch),
      "ohlc_mismatch_sample":ohlc_mismatch[:25],"volume_mismatch_sample":volume_mismatch[:25],
      "oi_mismatch_sample":oi_mismatch[:25],"max_abs_ohlc_diff":maxdiff,
      "provider_ohlc_digest":_digest(list(p.values()),4),"stored_ohlc_digest":_digest(list(s.values()),4),
      "checks":{
        "expiry_is_2026_08_31":str(contract.get("expiry_date") or "")[:10]=="2026-08-31",
        "timestamp_sets_equal":pkeys==skeys,
        "ohlc_equal_at_shared_timestamps":not ohlc_mismatch,
        "volume_equal_at_shared_timestamps":not volume_mismatch,
        "oi_equal_where_both_present":not oi_mismatch,
        "ohlc_digest_equal":_digest(list(p.values()),4)==_digest(list(s.values()),4),
      },
      "certification_scope":{
        "stored_vs_groww_historical_api":"AUDITED",
        "independent_mcx_exchange_source":"NOT_YET_CERTIFIED",
        "read_only":True,
        "note":"This audit performs no store upsert before comparison."
      }
    }
