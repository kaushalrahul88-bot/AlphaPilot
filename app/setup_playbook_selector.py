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


def playbook_selection_semantics(playbook:str|None, *, default_selector:bool=True)->dict:
    """Describe what a declared playbook means without changing the decision path.

    Current Mind's default selector chooses a regime-eligible playbook hypothesis. The
    generic evidence/geometry confirmation used by the thesis builder is not proof
    that the named chart pattern itself occurred. Literal pattern confirmation is a
    separate research/audit concern until explicitly integrated and validated.
    """
    base={
      "mode":"PLAYBOOK_SELECTION_SEMANTICS_V1",
      "declared_playbook":playbook,
      "decision_effect":"ANNOTATION_ONLY",
      "literal_pattern_confirmation":"NOT_VERIFIED_IN_DECISION_PATH" if playbook else "NOT_APPLICABLE",
      "generic_confirmation_is_literal_pattern_confirmation":False,
      "rule":"A named playbook is not pattern-confirmed merely because generic evidence and structural trade geometry are available.",
    }
    if not default_selector:
        return {**base,"status":"EXTERNAL_DECISION_BUILDER","selection_basis":"NOT_INFERRED"}
    if not playbook:
        return {**base,"status":"NO_DECLARED_PLAYBOOK","selection_basis":"REGIME_ELIGIBILITY_ONLY"}
    if playbook not in PLAYBOOKS:
        return {**base,"status":"UNKNOWN_DECLARED_PLAYBOOK","selection_basis":"UNKNOWN"}
    return {**base,"status":"REGIME_ELIGIBLE_HYPOTHESIS","selection_basis":"REGIME_ELIGIBILITY_ONLY"}
