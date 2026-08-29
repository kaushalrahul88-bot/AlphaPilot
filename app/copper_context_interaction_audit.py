"""Descriptive-only Copper context interaction audit.

This module does not select trades, fit thresholds to outcomes, or mutate Market Brain.
It asks whether point-in-time FX/CFTC context behaves differently across already-defined
Copper structure and volatility regimes. Any candidate must be preregistered later.
"""
from __future__ import annotations
from statistics import mean
from .commodity_time import parse_ist_timestamp
from .copper_context_feature_audit import descriptive_context_features
from .copper_research_brain import _brain_a_attribution_observations


def _stats(rows):
    values=[float(r["net_pct"]) for r in rows]
    wins=[v for v in values if v>0]; losses=[v for v in values if v<0]
    gp=sum(wins); gl=abs(sum(losses))
    return {
        "signals":len(values),
        "win_rate_pct":round(len(wins)/len(values)*100,2) if values else 0.0,
        "avg_net_return_pct":round(mean(values),4) if values else 0.0,
        "net_return_sum_pct":round(sum(values),4),
        "profit_factor":round(gp/gl,3) if gl>0 else None,
    }


def _level_bucket(percentile):
    if percentile is None:return "UNKNOWN"
    if percentile<=0.25:return "LOW"
    if percentile>=0.75:return "HIGH"
    return "MID"


def _magnitude_bucket(value, history):
    if value is None:return "UNKNOWN"
    eligible=[x for x in history if x is not None]
    if not eligible:return "UNKNOWN"
    p=sum(x<=value for x in eligible)/len(eligible)
    return "QUIET" if p<=0.33 else "LARGE" if p>=0.67 else "NORMAL"


def descriptive_context_interactions(experiences, store, horizon_minutes=60, round_trip_cost_bps=4.0, minimum_signals=8):
    observations=_brain_a_attribution_observations(experiences,horizon_minutes,round_trip_cost_bps)
    feature_report=descriptive_context_features(experiences,store,horizon_minutes,round_trip_cost_bps)
    daily={r["day"]:r for r in feature_report["rows"]}

    abs_history=[]; enriched=[]
    for row in sorted(observations,key=lambda r:str(r.get("timestamp") or "")):
        day=parse_ist_timestamp(row["timestamp"]).date().isoformat()
        ctx=daily.get(day) or {}
        magnitude=ctx.get("usdinr_abs_change_pct")
        if magnitude is not None:abs_history.append(float(magnitude))
        item=dict(row)
        item["fx_magnitude_bucket"]=_magnitude_bucket(magnitude,abs_history)
        item["fx_level_bucket"]=_level_bucket(ctx.get("usdinr_expanding_percentile"))
        item["cot_position_bucket"]=_level_bucket(ctx.get("cot_expanding_percentile"))
        change=ctx.get("cot_net_change")
        item["cot_change_direction"]="RISING" if change is not None and change>0 else "FALLING" if change is not None and change<0 else "FLAT_OR_UNKNOWN"
        enriched.append(item)

    specs=[
        ("FX_MAGNITUDE_X_STRUCTURE","fx_magnitude_bucket","structure"),
        ("FX_MAGNITUDE_X_VOLATILITY","fx_magnitude_bucket","atr_bucket"),
        ("FX_LEVEL_X_STRUCTURE","fx_level_bucket","structure"),
        ("CFTC_POSITION_X_STRUCTURE","cot_position_bucket","structure"),
        ("CFTC_POSITION_X_VOLATILITY","cot_position_bucket","atr_bucket"),
        ("CFTC_CHANGE_X_STRUCTURE","cot_change_direction","structure"),
    ]
    interactions=[]
    for name,a,b in specs:
        groups={}
        for row in enriched:
            key=(str(row.get(a) or "UNKNOWN"),str(row.get(b) or "UNKNOWN"))
            groups.setdefault(key,[]).append(row)
        cells=[]
        for (av,bv),group in sorted(groups.items()):
            stats=_stats(group)
            cells.append({a:av,b:bv,**stats,"adequate_sample":stats["signals"]>=minimum_signals})
        interactions.append({"interaction":name,"dimensions":[a,b],"cells":cells})

    return {
        "mode":"COPPER_CONTEXT_INTERACTION_AUDIT_V1",
        "research_only":True,
        "descriptive_only":True,
        "production_rules_changed":False,
        "minimum_signals_for_interpretation":int(minimum_signals),
        "observations":len(enriched),
        "interactions":interactions,
        "guardrails":[
            "No interaction cell selects or rejects trades.",
            "No cut-point is optimized on trade outcomes; FX magnitude uses expanding rank and existing market-state buckets are frozen.",
            "FX/CFTC context is sourced point-in-time through the existing 10:00 Asia/Kolkata availability policy.",
            "Sparse cells are reported but marked inadequate rather than promoted.",
            "Any candidate interaction requires a separately preregistered chronological out-of-sample test before Market Brain use.",
        ],
    }
