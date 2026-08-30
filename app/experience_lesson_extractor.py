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
            lessons.append("MISSED_MOVE_INVESTIGATION_REQUIRED: price moved materially without a valid setup; preserve the original abstention but investigate what caused the move, which precursors were knowable, which evidence was missing/misread, and whether a reusable earlier setup could have existed without hindsight.")
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


def investigate_missed_move(entry:dict, post_event_context:dict)->dict:
    """Forensic review after a large uncaptured move. Outcome data is review-only, never fed back into the frozen decision."""
    before=entry.get("information_board") or {}
    regime=entry.get("regime") or {}
    evidence=entry.get("evidence") or {}
    decision=entry.get("decision") or {}
    drivers=post_event_context.get("drivers",[])
    precursors=post_event_context.get("precursors",[])
    return {
      "mode":"MISSED_MOVE_FORENSIC_V1",
      "decision_fingerprint":entry.get("decision_fingerprint"),
      "original_action":decision.get("action"),
      "move":post_event_context.get("move"),
      "drivers":drivers,
      "precursors":[
        {"factor":p.get("factor"),"knowable_before_move":bool(p.get("knowable_before_move")),
         "available_at":p.get("available_at"),"captured_by_alphapilot":bool(p.get("captured_by_alphapilot")),
         "interpretation_at_time":p.get("interpretation_at_time")}
        for p in precursors],
      "failure_modes":{
        "DATA_GAP":[p for p in precursors if p.get("knowable_before_move") and not p.get("available_to_system")],
        "PERCEPTION_GAP":[p for p in precursors if p.get("available_to_system") and not p.get("captured_by_alphapilot")],
        "INTERPRETATION_GAP":[p for p in precursors if p.get("captured_by_alphapilot") and p.get("misinterpreted")],
        "STRATEGY_GAP":post_event_context.get("strategy_gap",[]),
        "UNFORESEEABLE_OR_NEW_INFORMATION":[p for p in precursors if not p.get("knowable_before_move")],
      },
      "counterfactual_question":"Using only information genuinely available before the move, was there a risk-defined setup AlphaPilot could reasonably have formed?",
      "pre_move_state":{"information_board":before,"regime":regime,"evidence":evidence},
      "guardrails":[
        "Do not label abstention wrong merely because price later moved.",
        "Do not use post-move information to invent a pre-move signal.",
        "If a precursor was knowable and repeatedly missed, create a research hypothesis.",
        "If the catalyst was genuinely unknowable, learn response/management behavior rather than pretend it was predictable.",
      ],
    }
