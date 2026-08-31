from __future__ import annotations

_DIRECTIONAL={"BULLISH","BEARISH"}
_ROLE_BY_LANE={
    "STRUCTURE":"ANCHOR",
    "PARTICIPATION":"CONFIRMATION",
    "VOLATILITY":"EXECUTION_CONTEXT",
    "GLOBAL_CONTEXT":"CONTEXT",
    "MACRO":"CONTEXT",
    "NEWS":"CONTEXT",
    "NEWS_REACTION":"CONTEXT",
    "EXPERIENCE":"MEMORY",
    "OPTIONS":"EXECUTION",
    "OTHER":"CONTEXT",
}


def evidence_role(lane:str)->str:
    return _ROLE_BY_LANE.get(str(lane or "OTHER").upper(),"CONTEXT")


def role_geometry(synthesis:dict)->dict:
    """Describe directional geometry without converting supporting evidence into votes.

    STRUCTURE is the directional anchor. Other lanes may confirm, contradict or
    contextualize that anchor, but cannot manufacture the underlying direction.
    This is outcome-blind and intentionally does not alter the V1 synthesis yet.
    """
    lanes=synthesis.get("lanes") or {}
    structure=[r for r in lanes.get("STRUCTURE",[]) if r.get("counts_for_direction",True)]
    stances={str(r.get("stance") or "UNKNOWN").upper() for r in structure}
    directional=stances & _DIRECTIONAL
    if len(directional)!=1:
        anchor=None
        anchor_state="MISSING" if not directional else "MIXED"
    else:
        anchor=next(iter(directional));anchor_state="SUPPORTED"

    confirmations=[];contradictions=[];context=[];execution=[];memory=[]
    for lane,rows in lanes.items():
        role=evidence_role(lane)
        for row in rows or []:
            stance=str(row.get("stance") or "UNKNOWN").upper()
            item={"lane":lane,"role":role,"stance":stance,"source":row.get("source")}
            if role in {"EXECUTION","EXECUTION_CONTEXT"}:execution.append(item);continue
            if role=="MEMORY":memory.append(item);continue
            if role=="CONTEXT":context.append(item)
            if not anchor or stance not in _DIRECTIONAL or not row.get("counts_for_direction",True):continue
            if stance==anchor:confirmations.append(item)
            else:contradictions.append(item)

    return {
        "mode":"TRADER_EVIDENCE_ROLES_V2_PREVIEW",
        "outcome_blind":True,
        "anchor_lane":"STRUCTURE",
        "anchor_direction":anchor,
        "anchor_state":anchor_state,
        "confirmations":confirmations,
        "contradictions":contradictions,
        "context":context,
        "execution":execution,
        "memory":memory,
        "actionable_direction":anchor,
        "rules":[
            "STRUCTURE anchors the underlying directional thesis.",
            "Supporting lanes cannot manufacture direction when STRUCTURE is missing or mixed.",
            "Context disagreement is preserved and does not become an equal directional vote.",
            "OPTIONS and VOLATILITY affect execution, not underlying direction.",
            "EXPERIENCE informs the prior but cannot override observable price structure.",
        ],
    }
