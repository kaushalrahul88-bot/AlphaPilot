from __future__ import annotations

REQUIRED_METRICS=("process_score","expectancy_r","false_positive_rate","abstention_quality","confidence_calibration")

def evaluate_learning_experiment(baseline:dict,candidate:dict,*,holdout_frozen:bool,point_in_time_clean:bool)->dict:
    missing=[m for m in REQUIRED_METRICS if baseline.get(m) is None or candidate.get(m) is None]
    if missing:return {"status":"REJECT","reason":"MISSING_REQUIRED_METRICS","missing":missing}
    if not holdout_frozen:return {"status":"REJECT","reason":"HOLDOUT_NOT_FROZEN"}
    if not point_in_time_clean:return {"status":"REJECT","reason":"POINT_IN_TIME_VIOLATION"}
    deltas={m:float(candidate[m])-float(baseline[m]) for m in REQUIRED_METRICS}
    reasons=[]
    if deltas["process_score"]<0:reasons.append("PROCESS_WORSE")
    if deltas["expectancy_r"]<=0:reasons.append("EXPECTANCY_NOT_IMPROVED")
    if deltas["false_positive_rate"]>0.02:reasons.append("FALSE_POSITIVES_INCREASED")
    if deltas["abstention_quality"]<-0.02:reasons.append("WAIT_NO_TRADE_QUALITY_WORSE")
    if deltas["confidence_calibration"]<-0.02:reasons.append("CALIBRATION_WORSE")
    return {"status":"REJECT" if reasons else "ELIGIBLE_FOR_HUMAN_REVIEW",
            "reason":";".join(reasons) if reasons else "MULTI_METRIC_IMPROVEMENT",
            "deltas":deltas,
            "rule":"No learned hypothesis auto-deploys. Passing experiments require explicit review before production promotion."}

def promotion_record(hypothesis:dict,evaluation:dict)->dict:
    return {"hypothesis":hypothesis,"evaluation":evaluation,
            "production_change_allowed":False,
            "next_step":"HUMAN_REVIEW" if evaluation.get("status")=="ELIGIBLE_FOR_HUMAN_REVIEW" else "KEEP_BASELINE"}
