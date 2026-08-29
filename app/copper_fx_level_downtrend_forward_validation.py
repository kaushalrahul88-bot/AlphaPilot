"""Forward validation for the preregistered Copper LOW-USDINR × DOWNTREND hypothesis.

Important: the original descriptive interaction audit inspected the full stored sample.
Therefore any 70/30 split of that same sample is diagnostic only, not untouched OOS.
True promotion eligibility begins only with observations strictly after DISCOVERY_CUTOFF.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .copper_context_feature_audit import descriptive_context_features
from .copper_research_brain import _brain_a_attribution_observations

DISCOVERY_CUTOFF = datetime(2026, 8, 29, 12, 16, 54, tzinfo=timezone.utc)
LOW_FX_PERCENTILE_MAX = 0.25
MIN_FORWARD_SIGNALS = 20
MIN_FORWARD_PROFIT_FACTOR = 1.10


def _stats(rows):
    values=[float(r["net_pct"]) for r in rows]
    wins=[v for v in values if v>0]
    losses=[v for v in values if v<0]
    gp=sum(wins); gl=abs(sum(losses))
    return {
        "signals":len(values),
        "wins":len(wins),
        "losses":len(losses),
        "win_rate_pct":round(len(wins)/len(values)*100,2) if values else 0.0,
        "avg_net_return_pct":round(mean(values),4) if values else 0.0,
        "net_return_sum_pct":round(sum(values),4),
        "profit_factor":round(gp/gl,3) if gl>0 else None,
    }


def _utc(value):
    dt=parse_ist_timestamp(value)
    return dt.astimezone(timezone.utc)


def _enrich(experiences, store, horizon_minutes, round_trip_cost_bps):
    observations=_brain_a_attribution_observations(experiences,horizon_minutes,round_trip_cost_bps)
    daily={r["day"]:r for r in descriptive_context_features(
        experiences,store,horizon_minutes,round_trip_cost_bps
    )["rows"]}
    out=[]
    for row in observations:
        if row.get("signal")!="SELL" or row.get("structure")!="DOWNTREND":
            continue
        day=parse_ist_timestamp(row["timestamp"]).date().isoformat()
        ctx=daily.get(day) or {}
        item=dict(row)
        item["usdinr_expanding_percentile"]=ctx.get("usdinr_expanding_percentile")
        out.append(item)
    return sorted(out,key=lambda r:_utc(r["timestamp"]))


def _is_low_fx(row):
    value=row.get("usdinr_expanding_percentile")
    return value is not None and float(value)<=LOW_FX_PERCENTILE_MAX


def _gate(baseline_rows, candidate_rows):
    baseline=_stats(baseline_rows)
    candidate=_stats(candidate_rows)
    pf=candidate["profit_factor"]
    checks={
        "minimum_20_signals":candidate["signals"]>=MIN_FORWARD_SIGNALS,
        "positive_avg_net_return":candidate["avg_net_return_pct"]>0,
        "profit_factor_gt_1_10":pf is not None and pf>MIN_FORWARD_PROFIT_FACTOR,
        "beats_contemporaneous_baseline":candidate["avg_net_return_pct"]>baseline["avg_net_return_pct"],
        "known_context_only":all(r.get("usdinr_expanding_percentile") is not None for r in candidate_rows),
    }
    return {
        "baseline":baseline,
        "candidate":candidate,
        "checks":checks,
        "passed":all(checks.values()),
    }


def validate_fx_level_downtrend(experiences, store, horizon_minutes=60, round_trip_cost_bps=4.0):
    rows=_enrich(experiences,store,horizon_minutes,round_trip_cost_bps)

    cut=max(1,min(len(rows),int(len(rows)*0.70))) if rows else 0
    retrospective=rows[cut:]
    retrospective_candidate=[r for r in retrospective if _is_low_fx(r)]
    retrospective_report=_gate(retrospective,retrospective_candidate)
    retrospective_report.update({
        "status":"DIAGNOSTIC_ONLY",
        "promotion_eligible":False,
        "reason":"The descriptive interaction audit already inspected this stored sample, so this split is not untouched OOS.",
        "split":{
            "total_eligible":len(rows),
            "earliest_70_pct":cut,
            "latest_30_pct":len(retrospective),
        },
    })

    forward=[r for r in rows if _utc(r["timestamp"])>DISCOVERY_CUTOFF]
    forward_candidate=[r for r in forward if _is_low_fx(r)]
    forward_report=_gate(forward,forward_candidate)
    enough=forward_report["candidate"]["signals"]>=MIN_FORWARD_SIGNALS
    forward_report.update({
        "status":"PASS" if forward_report["passed"] else "FAIL" if enough else "WAITING_FOR_FORWARD_SAMPLE",
        "promotion_eligible":bool(forward_report["passed"]),
        "discovery_cutoff_utc":DISCOVERY_CUTOFF.isoformat(),
        "hypothesis":"Brain-A SELL + DOWNTREND + point-in-time USD/INR expanding percentile <= 0.25",
    })

    return {
        "mode":"COPPER_FX_LEVEL_DOWNTREND_FORWARD_VALIDATION_V1",
        "research_only":True,
        "production_rules_changed":False,
        "horizon_minutes":int(horizon_minutes),
        "round_trip_cost_bps":float(round_trip_cost_bps),
        "retrospective_temporal_stress_test":retrospective_report,
        "true_forward_validation":forward_report,
        "guardrail":"Only observations strictly after the frozen discovery cutoff can satisfy the promotion gate. Retrospective 70/30 results are diagnostic only.",
    }
