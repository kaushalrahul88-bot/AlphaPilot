from __future__ import annotations

def extract_lessons(entry:dict)->dict:
    decision=entry.get("decision") or {}
    outcome=entry.get("outcome") or {}
    action=decision.get("action")
    process=entry.get("review") or {}
    lessons=[]
    if action in {"BUY_CE","BUY_PE"}:
        if outcome.get("result")=="STOP" and process.get("process_review")=="VALID":
            lessons.append("VALID_PROCESS_LOSS: preserve the setup logic; inspect regime, entry quality and normal variance before changing rules.")
        if outcome.get("result")=="TARGET" and process.get("process_review")=="INVALID":
            lessons.append("LUCKY_WIN: do not reinforce a process violation because it made money.")
        if outcome.get("mae_r") is not None and float(outcome["mae_r"])<=-1:
            lessons.append("ADVERSE_EXCURSION: inspect whether invalidation and entry location matched market structure.")
        if outcome.get("mfe_r") is not None and float(outcome["mfe_r"])>=1 and outcome.get("result")!="TARGET":
            lessons.append("EXIT_REVIEW: meaningful favorable excursion occurred without target capture; inspect management, not direction prediction.")
    elif action in {"WAIT","NO_TRADE"}:
        if outcome.get("clean_setup_later"):
            lessons.append("ABSTENTION_REVIEW: a later setup emerged; check whether waiting preserved better entry quality.")
        if outcome.get("future_move_without_setup"):
            lessons.append("GOOD_ABSTENTION_CANDIDATE: price moved but no valid setup existed at the decision time; do not learn to chase missed moves.")
    return {"decision_fingerprint":entry.get("decision_fingerprint"),"lessons":lessons,
      "guardrails":["Never convert one loss into a new rule.","Never reinforce a rule solely because one trade won.",
                    "Require repeated, comparable cases before proposing architecture/strategy changes.",
                    "Keep process defects separate from market randomness."]}

def aggregate_lessons(reviews:list[dict],minimum_recurrence:int=3)->dict:
    counts={}
    for r in reviews:
        for lesson in r.get("lessons",[]):counts[lesson.split(":")[0]]=counts.get(lesson.split(":")[0],0)+1
    recurrent={k:v for k,v in counts.items() if v>=minimum_recurrence}
    return {"counts":counts,"recurrent_patterns":recurrent,"eligible_for_hypothesis_review":sorted(recurrent),
      "rule":"Recurring lessons may create a research hypothesis; they do not auto-modify live trading logic."}
