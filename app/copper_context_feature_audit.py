"""Descriptive-only Copper external-context feature audit.

No trade selection, threshold fitting, or strategy promotion occurs here. The goal is to
represent USD/INR and CFTC positioning more faithfully before preregistering another test.
"""
from __future__ import annotations
from statistics import mean, median
from .copper_context_ablation_v2 import _day_context
from .copper_research_brain import _brain_a_attribution_observations
from .commodity_time import parse_ist_timestamp


def _num(v):
    try:return float(v)
    except (TypeError,ValueError):return None


def _pct_change(a,b):
    return None if a is None or b in (None,0) else (a/b-1.0)*100.0


def _cot_net(row):
    if not row:return None
    v=row.values or {}
    a=_num(v.get("m_money_positions_long_all")); b=_num(v.get("m_money_positions_short_all"))
    return None if a is None or b is None else a-b


def descriptive_context_features(experiences, store, horizon_minutes=60, round_trip_cost_bps=4.0):
    observations=_brain_a_attribution_observations(experiences,horizon_minutes,round_trip_cost_bps)
    by_day={}
    for row in observations:
        day=parse_ist_timestamp(row["timestamp"]).date().isoformat()
        by_day.setdefault(day,[]).append(row)
    days=sorted(by_day)
    rows=[]; fx_history=[]; cot_history=[]
    prior_fx=None; prior_cot=None
    for day in days:
        ctx=_day_context(store,day)
        fx=ctx.get("FX"); cot=ctx.get("POSITIONING")
        fxv=_num((fx.values or {}).get("usdinr")) if fx else None
        cotv=_cot_net(cot)
        fx_ret=_pct_change(fxv,prior_fx)
        cot_change=None if cotv is None or prior_cot is None else cotv-prior_cot
        if fxv is not None:fx_history.append(fxv)
        if cotv is not None and (not cot_history or cotv!=cot_history[-1]):cot_history.append(cotv)
        outcomes=[float(x["net_pct"]) for x in by_day[day]]
        rows.append({
          "day":day,"usdinr":fxv,"usdinr_change_pct":fx_ret,
          "usdinr_abs_change_pct":abs(fx_ret) if fx_ret is not None else None,
          "usdinr_expanding_percentile":round(sum(x<=fxv for x in fx_history)/len(fx_history),4) if fxv is not None else None,
          "cot_managed_money_net":cotv,"cot_net_change":cot_change,
          "cot_expanding_percentile":round(sum(x<=cotv for x in cot_history)/len(cot_history),4) if cotv is not None and cot_history else None,
          "observations":len(outcomes),"day_avg_net_pct":round(mean(outcomes),4) if outcomes else None,
          "day_median_net_pct":round(median(outcomes),4) if outcomes else None,
        })
        if fxv is not None:prior_fx=fxv
        if cotv is not None:prior_cot=cotv
    return {
      "mode":"COPPER_CONTEXT_FEATURE_AUDIT_V1","research_only":True,"descriptive_only":True,
      "rows":rows,
      "features":["usdinr_change_pct","usdinr_abs_change_pct","usdinr_expanding_percentile","cot_managed_money_net","cot_net_change","cot_expanding_percentile"],
      "guardrail":"No feature in this report selects trades or changes Market Brain. Any later rule must be preregistered and tested separately.",
    }
