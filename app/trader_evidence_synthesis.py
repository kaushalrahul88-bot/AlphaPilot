from __future__ import annotations

_DIRECTIONAL={"BULLISH","BEARISH"}


def _stance(row:dict)->str:
    return str(row.get("stance") or "UNKNOWN").upper()


def _lane(row:dict)->str:
    return str(row.get("lane") or "OTHER").upper()


def _annotate_directional_role(row:dict, raw_lanes:dict[str,list[dict]])->dict:
    """Preserve evidence while deciding whether it may count as directional confirmation.

    Normal evidence keeps its historical behaviour. Copper NEWS records carrying
    persistence metadata are treated more carefully: only fresh news that is
    confirmed by observable price structure can become an independent
    confirmation lane. Decayed/unconfirmed news remains visible as context.
    """
    item=dict(row)
    if _lane(item)!="NEWS":
        item["counts_for_direction"]=True
        return item

    detail=item.get("detail")
    if not isinstance(detail,dict) or "persistence" not in detail:
        item["counts_for_direction"]=True
        return item

    stance=_stance(item)
    visible=int(detail.get("visible") or 0)
    persistence=detail.get("persistence") or []
    statuses={str(p.get("status") or "").upper() for p in persistence if isinstance(p,dict)}
    price_stances={_stance(r) for r in raw_lanes.get("STRUCTURE",[]) if _stance(r) in _DIRECTIONAL}
    price_stance=next(iter(price_stances)) if len(price_stances)==1 else "MIXED" if price_stances else "UNKNOWN"

    if stance not in _DIRECTIONAL or visible<=0:
        state="NO_DIRECTIONAL_NEWS"
        counts=False
    elif "ACTIVE_DECAYED" in statuses and "ACTIVE" not in statuses:
        state=("PRICE_OVERRIDES_STALE_CATALYST"
               if price_stance in _DIRECTIONAL and price_stance!=stance
               else "NEWS_ABSORBED")
        counts=False
    elif "ACTIVE" in statuses and price_stance==stance:
        state="NEWS_CONFIRMED_BY_PRICE"
        counts=True
    else:
        state="NEWS_NOT_CONFIRMED_BY_PRICE"
        counts=False

    item["price_interaction_state"]=state
    item["price_stance_at_click"]=price_stance
    item["directional_role"]="CONFIRMATION" if counts else "CONTEXT_ONLY"
    item["counts_for_direction"]=counts
    return item


def synthesize_evidence(items:list[dict])->dict:
    """Organize independent evidence without reducing Trader Mind to a weighted prediction score."""
    raw_lanes={"STRUCTURE":[],"PARTICIPATION":[],"VOLATILITY":[],"GLOBAL_CONTEXT":[],"MACRO":[],"NEWS_REACTION":[],"NEWS":[],"OPTIONS":[],"OTHER":[]}
    for item in items:
        raw_lanes.setdefault(_lane(item),[]).append(item)

    lanes={k:[] for k in raw_lanes}
    contradictions=[]
    bullish=[];bearish=[];unknown=[]
    contextual=[];exclusions=[];news_interactions=[]
    for lane,rows in raw_lanes.items():
        annotated=[_annotate_directional_role(r,raw_lanes) for r in rows]
        lanes[lane]=annotated
        stances={_stance(r) for r in annotated}
        if "BULLISH" in stances and "BEARISH" in stances:contradictions.append(lane)
        for r in annotated:
            s=_stance(r)
            (bullish if s=="BULLISH" else bearish if s=="BEARISH" else unknown).append(r)
            if s in _DIRECTIONAL and not r.get("counts_for_direction",True):
                contextual.append(r)
                exclusions.append({"lane":lane,"stance":s,"source":r.get("source"),
                                   "reason":r.get("price_interaction_state") or "CONTEXT_ONLY"})
            if r.get("price_interaction_state"):
                news_interactions.append({"lane":lane,"stance":s,
                                          "state":r.get("price_interaction_state"),
                                          "price_stance_at_click":r.get("price_stance_at_click"),
                                          "directional_role":r.get("directional_role")})

    independent_bullish={_lane(r) for r in bullish if r.get("counts_for_direction",True)}
    independent_bearish={_lane(r) for r in bearish if r.get("counts_for_direction",True)}
    return {"mode":"TRADER_EVIDENCE_SYNTHESIS_V1","lanes":lanes,
      "bullish_evidence":bullish,"bearish_evidence":bearish,"unknown_evidence":unknown,
      "contextual_directional_evidence":contextual,
      "directional_exclusions":exclusions,
      "news_price_interactions":news_interactions,
      "contradictory_lanes":sorted(contradictions),
      "independent_bullish_lanes":sorted(independent_bullish),
      "independent_bearish_lanes":sorted(independent_bearish),
      "rules":["Do not sum evidence into a directional prediction score.",
               "Correlated indicators inside one lane are not independent confirmations.",
               "Contradictions must be preserved for the thesis, not averaged away.",
               "Missing/unknown evidence cannot support either direction.",
               "A trade thesis should prefer confirmation across genuinely different evidence lanes.",
               "Fresh NEWS becomes directional confirmation only when observable price structure confirms it.",
               "Unconfirmed, absorbed or decayed NEWS stays visible as context and cannot manufacture a trade trigger."]}


def evidence_quality(synthesis:dict)->str:
    b=len(synthesis.get("independent_bullish_lanes",[]));s=len(synthesis.get("independent_bearish_lanes",[]))
    conflicts=len(synthesis.get("contradictory_lanes",[]))
    dominant=max(b,s);opposing=min(b,s)
    if conflicts or (dominant and opposing>=dominant):return "CONFLICTED"
    if dominant>=4 and opposing<=1:return "STRONG"
    if dominant>=2:return "MODERATE"
    return "WEAK"
