from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .copper_market_brain_direction_audit import (
    HORIZON_BARS,
    PRIMARY_END,
    PRIMARY_START,
    REFERENCE_CONTRACT,
    _outcome,
    _session_quality,
)
from .copper_research_brain import (
    _build_copper_snapshot_clean,
    _precompute_information_quality,
    brain_a_signal,
    brain_b_signal,
    clean_ohlcv,
)

HORIZON_MINUTES = 60
MIN_STATE_OBSERVATIONS = 20
MIN_WINDOW_OBSERVATIONS = 8


def _bucket(value, cuts, labels):
    try:
        x=float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    for cut,label in zip(cuts,labels):
        if x<cut:
            return label
    return labels[-1]


def _dimensions(features, signal):
    stamp=parse_ist_timestamp(features["timestamp"])
    hour=stamp.hour
    session="MORNING" if hour<12 else "MIDDAY" if hour<16 else "EVENING"
    pos=features.get("session_range_position")
    try:
        pos=float(pos)
        location=(
            "LOWER_QUARTER" if pos<0.25 else
            "LOWER_MIDDLE" if pos<0.50 else
            "UPPER_MIDDLE" if pos<0.75 else
            "UPPER_QUARTER"
        )
    except (TypeError,ValueError):
        location="UNKNOWN"
    try:
        vwap=float(features.get("session_vwap_gap_pct"))
        vwap_loc=(
            "BELOW_FAR" if vwap<-0.15 else
            "BELOW_NEAR" if vwap<0 else
            "ABOVE_NEAR" if vwap<0.15 else
            "ABOVE_FAR"
        )
    except (TypeError,ValueError):
        vwap_loc="UNKNOWN"
    return {
        "signal":signal,
        "session":session,
        "structure":features.get("structure") or "UNKNOWN",
        "atr_bucket":_bucket(features.get("atr_pct"),[0.10,0.20,0.35],["LOW","NORMAL","HIGH","EXTREME"]),
        "momentum_bucket":_bucket(abs(float(features.get("return_15m_pct") or 0.0)),[0.03,0.08,0.15],["WEAK","NORMAL","STRONG","EXTREME"]),
        "relative_volume_bucket":_bucket(features.get("relative_volume"),[0.75,1.0,1.5],["QUIET","NORMAL","ACTIVE","SURGE"]),
        "session_location":location,
        "vwap_location":vwap_loc,
        "opening_range_break":features.get("opening_range_break") or "UNKNOWN",
        "price_oi_state":features.get("price_oi_state") or "UNKNOWN",
    }


def _stats(rows):
    if not rows:
        return {"observations":0,"direction_accuracy_pct":0.0,"avg_signed_forward_pct":0.0}
    signed=[float(r["signed_forward_pct"]) for r in rows]
    return {
        "observations":len(rows),
        "direction_accuracy_pct":round(sum(v>0 for v in signed)/len(signed)*100,2),
        "avg_signed_forward_pct":round(mean(signed),4),
        "avg_favorable_excursion_pct":round(mean(float(r["favorable_excursion_pct"]) for r in rows),4),
        "avg_adverse_excursion_pct":round(mean(float(r["adverse_excursion_pct"]) for r in rows),4),
    }


def _window_ids(rows, windows=3):
    ordered=sorted(rows,key=lambda r:r["timestamp"])
    n=len(ordered)
    result={}
    for i,row in enumerate(ordered):
        result[row["timestamp"]]=min(windows-1,int(i*windows/max(1,n)))
    return result


def _attribute(rows):
    dimensions=[
        "signal","session","structure","atr_bucket","momentum_bucket",
        "relative_volume_bucket","session_location","vwap_location",
        "opening_range_break","price_oi_state",
    ]
    window_map=_window_ids(rows,3)
    out={}
    stable_good=[]
    stable_bad=[]
    for dim in dimensions:
        groups=defaultdict(list)
        for row in rows:
            groups[row[dim]].append(row)
        dim_report={}
        for state,group in sorted(groups.items()):
            overall=_stats(group)
            if overall["observations"]<MIN_STATE_OBSERVATIONS:
                continue
            windows=[]
            for w in range(3):
                subset=[r for r in group if window_map.get(r["timestamp"])==w]
                windows.append({"window":w+1,**_stats(subset)})
            enough=all(x["observations"]>=MIN_WINDOW_OBSERVATIONS for x in windows)
            all_good=enough and all(x["direction_accuracy_pct"]>50 for x in windows)
            all_bad=enough and all(x["direction_accuracy_pct"]<50 for x in windows)
            payload={
                **overall,
                "windows":windows,
                "stable_above_50_pct":all_good,
                "stable_below_50_pct":all_bad,
            }
            dim_report[state]=payload
            candidate={"dimension":dim,"state":state,**payload}
            if all_good:
                stable_good.append(candidate)
            if all_bad:
                stable_bad.append(candidate)
        out[dim]=dim_report
    return {
        "dimensions":out,
        "stable_above_50_pct_states":stable_good,
        "stable_below_50_pct_states":stable_bad,
    }


def evaluate_error_attribution(candles,sample_every_bars=3):
    rows=clean_ohlcv(candles)
    quality=_session_quality(rows)
    info=_precompute_information_quality(rows)
    per_brain={"A":[],"B":[]}

    for index in range(50,len(rows),max(1,int(sample_every_bars))):
        stamp=parse_ist_timestamp(rows[index][0])
        dayq=quality.get(stamp.date()) or {}
        if not dayq.get("primary_score_eligible"):
            continue
        features=_build_copper_snapshot_clean(rows,index,information_quality=info)
        signals={"A":brain_a_signal(features),"B":brain_b_signal(features)}
        for brain,signal in signals.items():
            if signal=="NO_TRADE":
                continue
            outcome=_outcome(rows,index,signal,HORIZON_MINUTES)
            if outcome is None:
                continue
            per_brain[brain].append({
                "timestamp":stamp.isoformat(),
                **_dimensions(features,signal),
                **outcome,
            })

    return {
        "mode":"COPPER_MARKET_BRAIN_ERROR_ATTRIBUTION_V1",
        "research_only":True,
        "descriptive_only":True,
        "horizon_minutes":HORIZON_MINUTES,
        "same_session_only":True,
        "sparse_sessions_excluded":True,
        "strategy_rules_changed":False,
        "brain_a":{"overall":_stats(per_brain["A"]),**_attribute(per_brain["A"])},
        "brain_b":{"overall":_stats(per_brain["B"]),**_attribute(per_brain["B"])},
        "guardrails":[
            "Fixed pre-existing buckets only; no outcome-driven threshold search.",
            "States require at least 20 observations overall and 8 in each of three chronological windows before stability labels are allowed.",
            "Stable states are hypotheses, not trade filters.",
            "No option premium or futures P&L is inferred.",
        ],
    }


async def run_error_attribution_from_store(store,sample_every_bars=3):
    await store.initialize()
    segments=await store.read_symbol_contract_segments("COPPER",5,PRIMARY_START,PRIMARY_END)
    target=next((s for s in segments if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT),None)
    if not target:
        raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    candles=target.get("candles") or []
    report=evaluate_error_attribution(candles,sample_every_bars)
    report["reference_contract"]={
        "trading_symbol":target.get("trading_symbol"),
        "expiry_date":target.get("expiry_date"),
        "candles":len(candles),
    }
    return report
