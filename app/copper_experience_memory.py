from __future__ import annotations

from collections import Counter, defaultdict
from math import sqrt
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .copper_event_path_backtest import TARGET_PCT, STOP_PCT, _event_path
from .copper_market_brain_direction_audit import PRIMARY_END, PRIMARY_START, REFERENCE_CONTRACT, _session_quality
from .copper_research_brain import _build_copper_snapshot_clean, _precompute_information_quality, clean_ohlcv

FEATURES=(
    "return_15m_pct","return_60m_pct","ema20_gap_pct","ema50_gap_pct","atr_pct",
    "relative_volume","session_return_pct","session_range_position",
    "session_vwap_gap_pct","opening_range_position","time_adjusted_relative_volume",
)
CATEGORICAL=("structure","opening_range_break","price_oi_state")
MIN_PRIOR_EXPERIENCES=30
DEFAULT_K=75


def _f(v):
    try:return float(v)
    except (TypeError,ValueError):return None


def _snapshot_vector(features):
    return {k:_f(features.get(k)) for k in FEATURES}


def _scales(experiences):
    scales={}
    for k in FEATURES:
        vals=[e["vector"].get(k) for e in experiences if e["vector"].get(k) is not None]
        if len(vals)<2:
            scales[k]=1.0;continue
        m=mean(vals); var=mean((x-m)**2 for x in vals)
        scales[k]=max(sqrt(var),1e-9)
    return scales


def _distance(query,candidate,scales):
    parts=[]
    for k in FEATURES:
        a=query["vector"].get(k);b=candidate["vector"].get(k)
        if a is None or b is None:continue
        parts.append(((a-b)/scales[k])**2)
    for k in CATEGORICAL:
        a=query.get(k);b=candidate.get(k)
        if a and b and "UNKNOWN" not in {str(a),str(b)}:
            parts.append(0.0 if a==b else 1.0)
    return sqrt(sum(parts)/len(parts)) if parts else 999.0


def build_experiences(candles,sample_every_bars=3):
    rows=clean_ohlcv(candles);quality=_session_quality(rows);info=_precompute_information_quality(rows)
    out=[]
    for i in range(50,len(rows),max(1,int(sample_every_bars))):
        stamp=parse_ist_timestamp(rows[i][0])
        if not (quality.get(stamp.date()) or {}).get("primary_score_eligible"):continue
        f=_build_copper_snapshot_clean(rows,i,information_quality=info)
        for direction,signal in (("BULLISH","BUY"),("BEARISH","SELL")):
            path=_event_path(rows,i,signal,TARGET_PCT,STOP_PCT)
            if not path:continue
            out.append({
                "timestamp":stamp.isoformat(),"direction":direction,
                "vector":_snapshot_vector(f),
                "structure":f.get("structure"),"opening_range_break":f.get("opening_range_break"),
                "price_oi_state":f.get("price_oi_state"),
                "outcome":path["outcome"],"minutes_to_event":path["minutes_to_event"],
                "mfe_pct":path["mfe_pct"],"mae_pct":path["mae_pct"],
            })
    return out


def query_memory(experiences,current,k=DEFAULT_K):
    stamp=parse_ist_timestamp(current["timestamp"])
    prior=[e for e in experiences if parse_ist_timestamp(e["timestamp"])<stamp]
    if len(prior)<MIN_PRIOR_EXPERIENCES:
        return {"status":"INSUFFICIENT_MEMORY","prior_experiences":len(prior)}
    scales=_scales(prior)
    ranked=sorted(prior,key=lambda e:_distance(current,e,scales))[:max(1,min(int(k),len(prior)))]
    by_direction={}
    for direction in ("BULLISH","BEARISH"):
        sample=[e for e in ranked if e["direction"]==direction]
        resolved=[e for e in sample if e["outcome"]!="SESSION_END_NO_EVENT"]
        wins=[e for e in resolved if e["outcome"]=="TARGET_FIRST"]
        by_direction[direction]={
            "analogues":len(sample),"resolved":len(resolved),
            "target_first_pct_resolved":round(len(wins)/len(resolved)*100,2) if resolved else None,
            "avg_mfe_pct":round(mean(e["mfe_pct"] for e in sample),4) if sample else None,
            "avg_mae_pct":round(mean(e["mae_pct"] for e in sample),4) if sample else None,
            "outcomes":dict(Counter(e["outcome"] for e in sample)),
        }
    return {
        "status":"READY","prior_experiences":len(prior),"analogues_used":len(ranked),
        "nearest_distance":round(_distance(current,ranked[0],scales),4) if ranked else None,
        "by_direction":by_direction,
    }


def evaluate_walk_forward_memory(candles,sample_every_bars=3,k=DEFAULT_K):
    experiences=build_experiences(candles,sample_every_bars)
    queries=[]
    # One query per timestamp; outcome directions remain stored separately.
    bullish={e["timestamp"]:e for e in experiences if e["direction"]=="BULLISH"}
    for ts in sorted(bullish):
        e=bullish[ts]
        q={k_:e[k_] for k_ in ("timestamp","vector","structure","opening_range_break","price_oi_state")}
        result=query_memory(experiences,q,k)
        if result["status"]=="READY":queries.append({"timestamp":ts,**result})
    return {
        "mode":"COPPER_EXPERIENCE_MEMORY_V1","research_only":True,"production_rules_changed":False,
        "option_objective":"Memory estimates underlying direction/path evidence before CE/PE translation.",
        "target_pct":TARGET_PCT,"stop_pct":STOP_PCT,"analogue_k":k,
        "experiences":len(experiences),"walk_forward_queries":len(queries),
        "latest_queries":queries[-100:],
        "guardrails":[
            "Every memory lookup uses only experiences timestamped before the simulated decision.",
            "Similarity features are fixed before outcomes are inspected; no outcome-driven threshold search.",
            "Both bullish and bearish counterfactual paths are stored for research, not counted as executed trades.",
            "Memory output is evidence only and cannot mutate production strategy.",
            "No futures P&L or fabricated option premium is calculated.",
        ],
    }


async def run_experience_memory_from_store(store,sample_every_bars=3,k=DEFAULT_K):
    await store.initialize()
    segs=await store.read_symbol_contract_segments("COPPER",5,PRIMARY_START,PRIMARY_END)
    target=next((s for s in segs if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT),None)
    if not target:raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    candles=target.get("candles") or []
    report=evaluate_walk_forward_memory(candles,sample_every_bars,k)
    report["reference_contract"]={"trading_symbol":target.get("trading_symbol"),"candles":len(candles)}
    return report
