from __future__ import annotations

def synthesize_evidence(items:list[dict])->dict:
    """Organize independent evidence without reducing Trader Mind to a weighted prediction score."""
    lanes={"STRUCTURE":[],"PARTICIPATION":[],"VOLATILITY":[],"GLOBAL_CONTEXT":[],"MACRO":[],"NEWS_REACTION":[],"OPTIONS":[],"OTHER":[]}
    for item in items:
        lane=str(item.get("lane") or "OTHER").upper()
        lanes.setdefault(lane,[]).append(item)
    contradictions=[]
    bullish=[];bearish=[];unknown=[]
    for lane,rows in lanes.items():
        stances={str(r.get("stance") or "UNKNOWN").upper() for r in rows}
        if "BULLISH" in stances and "BEARISH" in stances:contradictions.append(lane)
        for r in rows:
            s=str(r.get("stance") or "UNKNOWN").upper()
            (bullish if s=="BULLISH" else bearish if s=="BEARISH" else unknown).append(r)
    independent_bullish={r.get("lane","OTHER") for r in bullish}
    independent_bearish={r.get("lane","OTHER") for r in bearish}
    return {"mode":"TRADER_EVIDENCE_SYNTHESIS_V1","lanes":lanes,
      "bullish_evidence":bullish,"bearish_evidence":bearish,"unknown_evidence":unknown,
      "contradictory_lanes":sorted(contradictions),
      "independent_bullish_lanes":sorted(independent_bullish),
      "independent_bearish_lanes":sorted(independent_bearish),
      "rules":["Do not sum evidence into a directional prediction score.",
               "Correlated indicators inside one lane are not independent confirmations.",
               "Contradictions must be preserved for the thesis, not averaged away.",
               "Missing/unknown evidence cannot support either direction.",
               "A trade thesis should prefer confirmation across genuinely different evidence lanes."]}

def evidence_quality(synthesis:dict)->str:
    b=len(synthesis.get("independent_bullish_lanes",[]));s=len(synthesis.get("independent_bearish_lanes",[]))
    conflicts=len(synthesis.get("contradictory_lanes",[]))
    dominant=max(b,s);opposing=min(b,s)
    if conflicts or (dominant and opposing>=dominant):return "CONFLICTED"
    if dominant>=4 and opposing<=1:return "STRONG"
    if dominant>=2:return "MODERATE"
    return "WEAK"
