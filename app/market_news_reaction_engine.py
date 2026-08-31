from __future__ import annotations

_DIRECTION={"BULLISH":1,"BEARISH":-1}


def _price(row):
    if not isinstance(row,dict):return None
    for key in ("close","price","last_price"):
        try:
            value=float(row[key])
            if value>0:return value
        except (KeyError,TypeError,ValueError):pass
    return None


def _move(a,b):
    x=_price(a);y=_price(b)
    return None if x is None or y is None else (y-x)/x


def _sign(move,noise):
    if move is None:return 0
    if move>noise:return 1
    if move<-noise:return -1
    return 0


def assess_market_news_reaction(event:dict,pre_event:dict|None,immediate:dict|None,
                                confirmation:dict|None=None,assimilation:dict|None=None,
                                noise_floor:float=0.0005)->dict:
    """Classify how price assimilates known news without using trade outcomes.

    Snapshots must be point-in-time observations selected by the caller. The engine
    never fetches data and never creates BUY/SELL actions.
    """
    stance=str(event.get("stance") or event.get("sentiment") or "UNKNOWN").upper()
    expected=_DIRECTION.get(stance)
    immediate_move=_move(pre_event,immediate)
    follow_move=_move(immediate,confirmation)
    assimilation_move=_move(pre_event,assimilation)
    first=_sign(immediate_move,noise_floor)
    follow=_sign(follow_move,noise_floor)
    assimilated=_sign(assimilation_move,noise_floor)

    if expected is None or immediate_move is None:
        state="UNOBSERVED"
    elif first==0:
        state="MUTED_REACTION"
    elif first==expected:
        if confirmation is None:
            state="INITIAL_CONFIRMATION"
        elif follow==-expected:
            state="FAILED_REACTION"
        elif follow in {0,expected}:
            state="ACCEPTED_REACTION"
        else:
            state="INITIAL_CONFIRMATION"
    else:
        state="MARKET_REJECTION"

    if state=="MUTED_REACTION" and assimilation is not None:
        if assimilated==expected:state="DELAYED_ACCEPTANCE"
        elif assimilated==-expected:state="DELAYED_REJECTION"
        else:state="ABSORBED_OR_PRICED_IN"
    elif state=="ACCEPTED_REACTION" and assimilation is not None and assimilated==-expected:
        state="REVERSAL_AFTER_ACCEPTANCE"

    return {
        "mode":"MARKET_NEWS_REACTION_V1",
        "outcome_blind":True,
        "headline_stance":stance,
        "expected_price_direction":("UP" if expected==1 else "DOWN" if expected==-1 else "UNKNOWN"),
        "reaction_state":state,
        "moves":{"immediate":immediate_move,"confirmation":follow_move,"assimilation":assimilation_move},
        "observations":{"pre_event":pre_event,"immediate":immediate,"confirmation":confirmation,"assimilation":assimilation},
        "rule":"News creates a market hypothesis; observable point-in-time market response determines whether that hypothesis is accepted, muted, rejected or reversed. This engine never creates a trade action.",
    }
