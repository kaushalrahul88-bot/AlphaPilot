from __future__ import annotations

PLAYBOOKS={
 "TREND_PULLBACK":{"needs":["TRENDING"],"avoid":["EXTENDED"],"description":"Join established trend only after controlled pullback/reacceptance and renewed participation."},
 "BREAKOUT_RETEST":{"needs":["OPENING_EXPANSION"],"avoid":["PARTICIPATION_FADING"],"description":"Trade expansion only after breakout acceptance/retest rather than blind chase."},
 "RANGE_EDGE_REVERSAL":{"needs":["RANGING"],"avoid":[],"description":"Consider reversal only near a meaningful range/value edge with rejection evidence."},
 "FAILED_BREAKOUT":{"needs":[],"avoid":[],"description":"Trade failure only after price returns through the failed level and confirms rejection."},
}

def eligible_playbooks(regime_labels:list[str])->dict:
    labels=set(regime_labels)
    eligible=[];blocked=[]
    for name,p in PLAYBOOKS.items():
        missing=[x for x in p["needs"] if x not in labels]
        conflicts=[x for x in p["avoid"] if x in labels]
        row={"playbook":name,"description":p["description"],"missing_regime":missing,"conflicts":conflicts}
        (eligible if not missing and not conflicts else blocked).append(row)
    return {"mode":"SETUP_PLAYBOOK_SELECTOR_V1","eligible":eligible,"blocked":blocked,
      "rule":"Playbooks generate hypotheses and required confirmations, never automatic CE/PE orders."}

def setup_candidate(playbook:str, *, thesis=None,confirmation=None,entry_trigger=None,invalidation=None,target_logic=None):
    if playbook not in PLAYBOOKS:return {"status":"REJECTED","reason":"UNKNOWN_PLAYBOOK"}
    missing=[k for k,v in {"thesis":thesis,"confirmation":confirmation,"entry_trigger":entry_trigger,"invalidation":invalidation,"target_logic":target_logic}.items() if not v]
    return {"status":"READY_FOR_RISK_REVIEW" if not missing else "WAIT","playbook":playbook,"missing":missing,
            "thesis":thesis,"confirmation":confirmation,"entry_trigger":entry_trigger,"invalidation":invalidation,"target_logic":target_logic}
