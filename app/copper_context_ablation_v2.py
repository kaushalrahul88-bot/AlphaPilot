"""Preregistered point-in-time Copper context ablation.

Frozen variants:
A = existing expanding-day baseline.
B = A hypotheses, but only signals whose D-1 FX direction agrees with trade direction.
C = B plus CFTC managed-money net-position direction agreement.

No thresholds are optimized on outcomes. Context values are joined only when available_at
is no later than the simulated decision time.
"""
from __future__ import annotations
from statistics import mean
from .commodity_time import parse_ist_timestamp
from .copper_research_brain import expanding_daily_edge_backtest, _brain_a_attribution_observations, DAILY_EDGE_DIMENSIONS, _segment_stats, _f


def _day_context(store, day, decision_hour=10):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    decision=datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Kolkata"),hour=decision_hour)
    rows=store.read_available("COPPER",decision,("FX","POSITIONING"))
    latest={}
    for row in rows:
        if row.kind not in latest or row.available_at > latest[row.kind].available_at:
            latest[row.kind]=row
    return latest


def _fx_direction(current, previous):
    if not current or not previous:return 0
    a=_f((current.values or {}).get("usdinr")); b=_f((previous.values or {}).get("usdinr"))
    if a is None or b is None:return 0
    return 1 if a>b else -1 if a<b else 0


def _cot_direction(current, previous):
    if not current or not previous:return 0
    def net(row):
        v=row.values or {}
        long=_f(v.get("m_money_positions_long_all")); short=_f(v.get("m_money_positions_short_all"))
        return None if long is None or short is None else long-short
    a,b=net(current),net(previous)
    if a is None or b is None:return 0
    return 1 if a>b else -1 if a<b else 0


def _signal_direction(signal):
    return 1 if signal=="BUY" else -1 if signal=="SELL" else 0


def _aggregate(rows):
    values=[float(x["net_pct"]) for x in rows]
    wins=[x for x in values if x>0]; losses=[x for x in values if x<0]
    gp=sum(wins); gl=abs(sum(losses))
    return {
      "signals":len(values),"wins":len(wins),"losses":len(losses),
      "win_rate_pct":round(len(wins)/len(values)*100,2) if values else 0.0,
      "avg_net_return_pct":round(mean(values),4) if values else 0.0,
      "net_return_sum_pct":round(sum(values),4),
      "profit_factor":round(gp/gl,3) if gl>0 else None,
    }


def context_ablation(experiences, store, horizon_minutes=60, round_trip_cost_bps=4.0, minimum_training_signals=20):
    observations=_brain_a_attribution_observations(experiences,horizon_minutes,round_trip_cost_bps)
    by_day={}
    for row in observations:
        day=parse_ist_timestamp(row["timestamp"]).date().isoformat()
        by_day.setdefault(day,[]).append(row)
    days=sorted(by_day)
    training=[]
    variants={"A":[],"B":[],"C":[]}
    daily=[]
    prior_fx=prior_cot=None

    for index,day in enumerate(days):
        today=by_day[day]
        context=_day_context(store,day)
        fx=context.get("FX"); cot=context.get("POSITIONING")
        fx_dir=_fx_direction(fx,prior_fx)
        cot_dir=_cot_direction(cot,prior_cot)
        if index==0:
            training.extend(today); prior_fx=fx or prior_fx; prior_cot=cot or prior_cot; continue
        hypotheses=[]
        for dimension in DAILY_EDGE_DIMENSIONS:
            groups={}
            for row in training:groups.setdefault(str(row.get(dimension,"UNKNOWN")),[]).append(row)
            for value,group in groups.items():
                stats=_segment_stats(group); pf=_f(stats.get("profit_factor"),0.0) or 0.0
                if stats["signals"]>=minimum_training_signals and stats["avg_net_return_pct"]>0 and pf>1:
                    hypotheses.append((dimension,value))
        matched=[]
        for row in today:
            if any(str(row.get(d,"UNKNOWN"))==v for d,v in hypotheses):matched.append(row)
        a=list(matched)
        # USD/INR up is treated as supportive for MCX INR-denominated Copper BUY; down supports SELL.
        b=[r for r in a if fx_dir and _signal_direction(r.get("signal"))==fx_dir]
        # Managed-money net positioning must be changing in the same direction for C.
        cc=[r for r in b if cot_dir and _signal_direction(r.get("signal"))==cot_dir]
        variants["A"].extend(a); variants["B"].extend(b); variants["C"].extend(cc)
        daily.append({"test_day":day,"fx_direction":fx_dir,"cot_net_direction":cot_dir,
                      "A_signals":len(a),"B_signals":len(b),"C_signals":len(cc)})
        training.extend(today); prior_fx=fx or prior_fx; prior_cot=cot or prior_cot

    return {
      "mode":"COPPER_CONTEXT_ABLATION_V1","research_only":True,
      "preregistered_rules":{
        "A":"Frozen existing expanding-day hypotheses.",
        "B":"A plus sign agreement with latest point-in-time USD/INR change; no optimized threshold.",
        "C":"B plus sign agreement with latest point-in-time CFTC managed-money net-position change; no optimized threshold.",
      },
      "variants":{k:_aggregate(v) for k,v in variants.items()},
      "daily_results":daily,
      "guardrails":["No outcome-derived context thresholds.","available_at must precede decision time.","Baseline hypothesis discovery is identical across A/B/C.","Context can only filter baseline signals in v1; it cannot create new signals."],
    }
