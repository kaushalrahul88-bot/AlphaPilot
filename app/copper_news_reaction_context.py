from __future__ import annotations
from .commodity_time import parse_ist_timestamp

def assess_news_reaction(event:dict, market_before:dict|None, market_after:dict|None)->dict:
    """Describe market confirmation/contradiction; never turn a headline into a trade."""
    stance=str(event.get("stance") or event.get("sentiment") or "UNKNOWN").upper()
    if not market_before or not market_after:
        reaction="UNOBSERVED"
    else:
        b=float(market_before.get("price"))
        a=float(market_after.get("price"))
        move=(a-b)/b if b else 0.0
        if abs(move)<0.0005: reaction="MUTED"
        elif move>0: reaction="PRICE_UP"
        else: reaction="PRICE_DOWN"
    expected={"BULLISH":"PRICE_UP","BEARISH":"PRICE_DOWN"}.get(stance)
    confirmation="UNKNOWN" if reaction in {"UNOBSERVED","MUTED"} or expected is None else ("CONFIRMED" if reaction==expected else "CONTRADICTED")
    return {"event":event,"headline_stance":stance,"market_reaction":reaction,
            "confirmation":confirmation,
            "rule":"Headline meaning and market reaction are separate evidence. Neither alone creates BUY_CE/BUY_PE."}

def news_visible_as_of(events:list[dict],click_timestamp:str)->list[dict]:
    click=parse_ist_timestamp(click_timestamp)
    visible=[]
    for e in events:
        ts=e.get("available_at") or e.get("published_at")
        if ts and parse_ist_timestamp(ts)<=click: visible.append(e)
    return sorted(visible,key=lambda x:parse_ist_timestamp(x.get("available_at") or x.get("published_at")))
