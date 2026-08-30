from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

BULLISH_SUPPLY=("ban","bans","shutdown","closure","strike","disruption","supply cut","output cut","production cut","inventory fall","inventories fall","stocks fall","shortage")
BEARISH_SUPPLY=("restart","restarts","reopen","reopens","production rises","output rises","supply rises","inventory rises","inventories rise","stocks rise")
BULLISH_DEMAND=("stimulus","demand rises","demand growth","imports rise","manufacturing expands","pmi above 50")
BEARISH_DEMAND=("demand falls","imports fall","manufacturing contracts","pmi below 50","recession")
PRICE_RECAP=("price surge","prices surge","price rises","prices rise","price rally","prices rally","record-high copper prices","record high copper prices","copper gains","copper jumps","copper climbs","copper falls","copper drops","copper slides")
NON_COMMODITY=("basketball","scores ","mercury beat","wire","cable","theft","stolen","stealing","swan boats","antenna","coil","manure","contamination","ofs","share price","shares ","ipo","private placement")
MAJOR_SUPPLY=("codelco","escondida","grasberg","collahuasi","las bambas","kamoa","tenke","chuquicamata","freeport","antofagasta","glencore","bhp","rio tinto","southern copper","congo","drc")
MECHANISM_TERMS={
    "SUPPLY":("supply","mine","smelter","refinery","production","output","concentrate","inventory","inventories","warehouse","stocks","export ban","ban","strike","shutdown","closure","disruption"),
    "DEMAND":("demand","imports","manufacturing","pmi","stimulus","construction","grid","electric vehicle","ev "),
    "MACRO":("fed","federal reserve","dollar","rates","tariff","tariffs","trade war","china","chinese"),
}

def _contains_any(text:str, terms:Iterable[str])->bool:
    return any(term in text for term in terms)

def assess_copper_news(record:dict)->dict:
    value=record.get("value") or {}
    headline=str(value.get("headline") or "").strip()
    low=headline.lower()
    result={
        "headline":headline,
        "available_at":record.get("available_at"),
        "source":record.get("source"),
        "entity_class":"UNKNOWN",
        "commodity_relevance":"UNKNOWN",
        "transmission_mechanism":"NONE",
        "materiality":"UNKNOWN",
        "novelty":"UNKNOWN",
        "effect":"UNKNOWN",
        "confidence":0.0,
        "disposition":"BLOCK",
        "reasons":[],
    }
    if not headline:
        result["reasons"].append("MISSING_HEADLINE")
        return result
    if "copper" not in low:
        result["reasons"].append("NO_COPPER_REFERENCE")
        return result
    if _contains_any(low,NON_COMMODITY):
        result.update(entity_class="NON_COMMODITY_OR_EQUITY_CONTEXT",commodity_relevance="LOW",materiality="LOW",novelty="IRRELEVANT",confidence=0.99,disposition="BLOCK")
        result["reasons"].append("NO_COMMODITY_MARKET_TRANSMISSION")
        return result
    if _contains_any(low,PRICE_RECAP):
        result.update(entity_class="COPPER_COMMODITY",commodity_relevance="HIGH",transmission_mechanism="PRICE_RECAP",materiality="CONTEXT",novelty="NON_INDEPENDENT",confidence=0.95,disposition="CONTEXT_ONLY")
        result["reasons"].append("PRICE_MOVE_ALREADY_OBSERVED_NOT_INDEPENDENT_CAUSAL_NEWS")
        return result

    mechanism="NONE"
    for name,terms in MECHANISM_TERMS.items():
        if _contains_any(low,terms):
            mechanism=name
            break
    result["transmission_mechanism"]=mechanism
    result["entity_class"]="COPPER_COMMODITY"
    result["commodity_relevance"]="HIGH" if mechanism!="NONE" else "MEDIUM"

    effect="UNKNOWN"
    if _contains_any(low,BULLISH_SUPPLY) or _contains_any(low,BULLISH_DEMAND):
        effect="BULLISH"
    elif _contains_any(low,BEARISH_SUPPLY) or _contains_any(low,BEARISH_DEMAND):
        effect="BEARISH"
    result["effect"]=effect

    major=_contains_any(low,MAJOR_SUPPLY)
    materiality="HIGH" if major and mechanism=="SUPPLY" else "MEDIUM" if mechanism!="NONE" else "LOW"
    result["materiality"]=materiality
    result["novelty"]="POTENTIALLY_NEW" if mechanism!="NONE" else "UNPROVEN"

    if mechanism=="NONE":
        result.update(confidence=0.85,disposition="BLOCK")
        result["reasons"].append("NO_EXPLICIT_TRANSMISSION_MECHANISM")
    elif effect=="UNKNOWN":
        result.update(confidence=0.80,disposition="CONTEXT_ONLY")
        result["reasons"].append("DIRECTIONAL_EFFECT_NOT_DEFENSIBLE_FROM_HEADLINE")
    elif materiality=="LOW":
        result.update(confidence=0.85,disposition="BLOCK")
        result["reasons"].append("INSUFFICIENT_MATERIALITY")
    else:
        result.update(confidence=0.88 if materiality=="HIGH" else 0.78,disposition="ALLOW")
        result["reasons"].append("EXPLICIT_CAUSAL_TRANSMISSION_AND_DIRECTION")
    return result

def apply_news_intelligence(records:list[dict])->dict:
    assessments=[assess_copper_news(r) for r in records or []]
    by_key={(a["available_at"],a["headline"]):a for a in assessments}
    allowed=[]
    context_only=[]
    blocked=[]
    for r in records or []:
        v=r.get("value") or {}
        a=by_key.get((r.get("available_at"),str(v.get("headline") or "")))
        enriched={**r,"news_intelligence":a}
        if a and a["disposition"]=="ALLOW":allowed.append(enriched)
        elif a and a["disposition"]=="CONTEXT_ONLY":context_only.append(enriched)
        else:blocked.append(enriched)
    return {
        "mode":"COPPER_NEWS_INTELLIGENCE_V1",
        "assessments":assessments,
        "allowed_records":allowed,
        "context_only_records":context_only,
        "blocked_records":blocked,
        "counts":{
            "ALLOW":len(allowed),
            "CONTEXT_ONLY":len(context_only),
            "BLOCK":len(blocked),
        },
        "policy":"Only ALLOW records can provide directional NEWS evidence to Market Brain. CONTEXT_ONLY is non-voting. BLOCK is invisible to directional synthesis.",
    }
