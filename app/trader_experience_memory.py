from __future__ import annotations

def memory_case(journal_entry:dict)->dict:
    d=journal_entry.get("decision",{});o=journal_entry.get("outcome") or {};r=journal_entry.get("review") or {}
    return {"decision_fingerprint":journal_entry.get("decision_fingerprint"),
      "regime":journal_entry.get("regime"),"evidence":journal_entry.get("evidence"),
      "thesis":journal_entry.get("thesis"),"action":d.get("action"),"outcome":o,
      "process_review":r.get("process_review"),"lessons":r.get("lessons",[]),
      "rule":"Memory stores situation, decision process and outcome separately."}

def retrieve_similar(cases:list[dict],current:dict,limit:int=5)->list[dict]:
    """Transparent similarity by shared regime/evidence descriptors; never peek at future current outcome."""
    current_labels=set((current.get("regime") or {}).get("regime_labels",[]))
    current_lanes=set((current.get("evidence") or {}).get("independent_bullish_lanes",[]))|set((current.get("evidence") or {}).get("independent_bearish_lanes",[]))
    scored=[]
    for c in cases:
        labels=set((c.get("regime") or {}).get("regime_labels",[]))
        ev=c.get("evidence") or {}
        lanes=set(ev.get("independent_bullish_lanes",[]))|set(ev.get("independent_bearish_lanes",[]))
        score=2*len(current_labels&labels)+len(current_lanes&lanes)
        if score:scored.append((score,c))
    scored.sort(key=lambda x:x[0],reverse=True)
    return [dict(c,similarity_score=s) for s,c in scored[:limit]]

def summarize_experience(cases:list[dict])->dict:
    actions={}
    for c in cases:actions[c.get("action")]=actions.get(c.get("action"),0)+1
    return {"sample_size":len(cases),"actions":actions,
      "warning":"Historical similarity informs expectations and failure modes; it does not dictate the current trade."}
