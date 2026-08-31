from __future__ import annotations


def _number(row:dict|None,key:str):
    if not isinstance(row,dict):return None
    try:return float(row[key]) if row.get(key) is not None else None
    except (TypeError,ValueError):return None


def _change(before,after,key):
    a=_number(before,key);b=_number(after,key)
    if a is None or b is None or a==0:return None
    return (b-a)/abs(a)


def _volume_observation(window:dict):
    """Return outcome-blind activity change and provenance for volume confirmation."""
    activity=window.get("volume_activity")
    immediate=window.get("immediate")
    if isinstance(activity,dict):
        baseline=_number(activity,"median_bar_volume")
        observed=_number(immediate,"bar_volume")
        if baseline is None or observed is None or baseline<=0:
            return None,{"basis":"NORMALIZED_BAR_VOLUME","semantics":activity.get("semantics"),
                         "baseline_median_bar_volume":baseline,"observed_bar_volume":observed}
        return (observed-baseline)/baseline,{"basis":"NORMALIZED_BAR_VOLUME",
                                            "semantics":activity.get("semantics"),
                                            "baseline_median_bar_volume":baseline,
                                            "observed_bar_volume":observed,
                                            "baseline_bars_used":activity.get("baseline_bars_used")}
    # Backward-compatible path for callers that predate explicit volume semantics.
    pre=window.get("pre_event")
    return _change(pre,immediate,"volume"),{"basis":"LEGACY_RAW_SNAPSHOT_CHANGE","semantics":"UNDECLARED"}


def assess_news_participation(window:dict, reaction:dict, *, volume_expansion:float=0.25,
                              oi_expansion:float=0.01)->dict:
    """Describe participation behind a news reaction without creating a trade signal.

    Thresholds are explicit engineering defaults, not fitted against trade outcomes.
    Missing or ambiguous volume/OI remains UNKNOWN rather than being treated as
    confirmation. When a reaction window declares normalized activity metadata, raw
    session-cumulative volume is never compared directly across timestamps.
    """
    pre=window.get("pre_event");immediate=window.get("immediate");confirmation=window.get("confirmation")
    volume_change,volume_meta=_volume_observation(window)
    oi_change=_change(pre,confirmation or immediate,"open_interest")
    volume_state="UNKNOWN" if volume_change is None else "EXPANDED" if volume_change>=volume_expansion else "NOT_EXPANDED"
    oi_state="UNKNOWN" if oi_change is None else "EXPANDED" if oi_change>=oi_expansion else "NOT_EXPANDED"
    reaction_state=str(reaction.get("reaction_state") or "UNOBSERVED")
    accepted=reaction_state in {"ACCEPTED_REACTION","DELAYED_ACCEPTANCE","INITIAL_CONFIRMATION"}
    rejected=reaction_state in {"FAILED_REACTION","MARKET_REJECTION","DELAYED_REJECTION","REVERSAL_AFTER_ACCEPTANCE"}
    confirmations=sum(x=="EXPANDED" for x in (volume_state,oi_state))

    if accepted and confirmations==2:state="BROAD_PARTICIPATION"
    elif accepted and confirmations==1:state="PARTIAL_PARTICIPATION"
    elif accepted:state="PRICE_ONLY_ACCEPTANCE"
    elif rejected and confirmations:state="PARTICIPATED_BUT_REJECTED"
    elif rejected:state="REJECTION_WITHOUT_PARTICIPATION_CONFIRMATION"
    else:state="INCONCLUSIVE_PARTICIPATION"

    return {"mode":"MARKET_NEWS_PARTICIPATION_V1","outcome_blind":True,"participation_state":state,
            "reaction_state":reaction_state,"volume":{**volume_meta,"change":volume_change,"state":volume_state},
            "open_interest":{"change":oi_change,"state":oi_state},
            "rules":["Price reaction and participation are separate observations.",
                     "Missing or ambiguous volume or OI cannot confirm participation.",
                     "Session-cumulative volume must be normalized to bar activity before participation comparison.",
                     "Volume/OI confirmation cannot manufacture a directional trade thesis.",
                     "No trade outcome or P&L is consulted."]}
