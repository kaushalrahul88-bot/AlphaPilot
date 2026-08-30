from __future__ import annotations

REQUIRED=("direction","thesis","entry_trigger","invalidation","target_logic","risk_reward_basis","contradictions","evidence_quality")

def build_trade_thesis(**kwargs):
    x={k:kwargs.get(k) for k in REQUIRED}
    missing=[k for k,v in x.items() if v is None or v==""]
    x["complete"]=not missing;x["missing"]=missing
    x["instrument_intent"]="BUY_CE" if x.get("direction")=="BULLISH" else ("BUY_PE" if x.get("direction")=="BEARISH" else None)
    return x

def gate_trade_thesis(thesis:dict, *, confirmation_pending=False, material_context_missing=False):
    if confirmation_pending:return {"action":"WAIT","reason":"CONFIRMATION_PENDING"}
    if not thesis.get("complete"):return {"action":"NO_TRADE","reason":"INCOMPLETE_THESIS"}
    if material_context_missing and thesis.get("evidence_quality") not in {"STRONG","VERY_STRONG"}:
        return {"action":"NO_TRADE","reason":"MATERIAL_CONTEXT_MISSING"}
    if thesis.get("direction") not in {"BULLISH","BEARISH"}:
        return {"action":"NO_TRADE","reason":"NO_DIRECTIONAL_OPPORTUNITY"}
    return {"action":thesis["instrument_intent"],"reason":"RISK_DEFINED_ACTIONABLE_THESIS","thesis":thesis}
