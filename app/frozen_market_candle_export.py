from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .commodity_time import parse_ist_timestamp


def _canonical_json(value)->str:
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)


def _normalise_candle(row)->dict:
    if isinstance(row,dict):
        timestamp=row.get("timestamp") or row.get("time") or row.get("datetime")
        values={k:row.get(k) for k in ("open","high","low","close","volume")}
        oi=row.get("open_interest") if "open_interest" in row else row.get("oi")
    else:
        if len(row)<6:raise ValueError("OHLCV row requires timestamp, open, high, low, close, volume")
        timestamp=row[0];values=dict(zip(("open","high","low","close","volume"),row[1:6]));oi=row[6] if len(row)>6 else None
    if not timestamp:raise ValueError("candle timestamp is required")
    ts=parse_ist_timestamp(timestamp)
    out={"timestamp":ts.isoformat(),**values}
    if oi is not None:out["open_interest"]=oi
    return out


def build_frozen_candle_artifact(candles,*,symbol:str,trading_symbol:str,interval_minutes:int,
                                 start,end,source:str="persistent_store",exported_at:str|None=None)->dict:
    """Build a deterministic, checksum-protected candle artifact without network access."""
    start_ts=parse_ist_timestamp(start);end_ts=parse_ist_timestamp(end)
    if end_ts<start_ts:raise ValueError("end must not precede start")
    rows=[]
    for raw in candles:
        row=_normalise_candle(raw);ts=parse_ist_timestamp(row["timestamp"])
        if start_ts<=ts<=end_ts:rows.append(row)
    rows.sort(key=lambda r:parse_ist_timestamp(r["timestamp"]))
    timestamps=[r["timestamp"] for r in rows]
    if len(timestamps)!=len(set(timestamps)):raise ValueError("duplicate candle timestamps in frozen export")
    payload_sha256=hashlib.sha256(_canonical_json(rows).encode()).hexdigest()
    return {"mode":"FROZEN_MARKET_CANDLES_V1","symbol":symbol,"trading_symbol":trading_symbol,
            "interval_minutes":int(interval_minutes),"start":start_ts.isoformat(),"end":end_ts.isoformat(),
            "source":source,"point_in_time":True,"network_refetch":False,"candle_count":len(rows),
            "first_timestamp":timestamps[0] if timestamps else None,"last_timestamp":timestamps[-1] if timestamps else None,
            "candles_sha256":payload_sha256,
            "exported_at":exported_at or datetime.now(timezone.utc).isoformat(),"candles":rows,
            "guardrails":["Candles are copied from the supplied stored segment only.",
                          "No historical market data is fetched during export.",
                          "The checksum covers the canonical ordered candle payload."]}
