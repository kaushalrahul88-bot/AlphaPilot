from __future__ import annotations
from collections import defaultdict

GAP_TYPES=("DATA_GAP","PERCEPTION_GAP","INTERPRETATION_GAP","STRATEGY_GAP","UNFORESEEABLE_OR_NEW_INFORMATION")

def build_missed_move_hypotheses(forensics:list[dict], minimum_cases:int=3)->dict:
    """Promote repeated pre-move gaps to research hypotheses, never directly to trading rules."""
    patterns=defaultdict(list)
    for review in forensics:
        fp=review.get("decision_fingerprint")
        for gap in GAP_TYPES:
            rows=(review.get("failure_modes") or {}).get(gap,[]) or []
            for row in rows:
                factor=str(row.get("factor") if isinstance(row,dict) else row)
                patterns[(gap,factor)].append(fp)
    hypotheses=[]
    for (gap,factor),cases in patterns.items():
        unique=[x for x in dict.fromkeys(cases) if x]
        if len(unique)<minimum_cases:continue
        hypotheses.append({
          "gap_type":gap,"factor":factor,"supporting_cases":unique,"case_count":len(unique),
          "research_question":f"Can AlphaPilot improve recognition/handling of {factor} without hindsight or degrading out-of-sample trade quality?",
          "status":"RESEARCH_ONLY",
          "required_validation":["point_in_time_feature_definition","holdout_replay","process_score_comparison",
                                 "expectancy_R_comparison","false_positive_cost","WAIT_NO_TRADE_quality"],
        })
    return {"mode":"MISSED_MOVE_RESEARCH_HYPOTHESES_V1","minimum_cases":minimum_cases,
            "hypotheses":hypotheses,
            "rule":"Recurring missed-move patterns may trigger research, but cannot auto-change Market Brain or Option Brain."}

def approve_for_experiment(hypothesis:dict)->dict:
    return {"status":"EXPERIMENT_CANDIDATE","hypothesis":hypothesis,
      "guardrails":["Freeze baseline before testing.","Do not tune on holdout outcomes.",
                    "Reject improvements that merely increase trade frequency or hindsight capture.",
                    "Promote only if process quality and risk-adjusted results improve without material calibration damage."]}
