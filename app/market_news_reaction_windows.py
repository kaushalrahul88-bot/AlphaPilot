from __future__ import annotations

from datetime import timedelta
from .commodity_time import parse_ist_timestamp


def _ts(row:dict):
    value=row.get("timestamp") or row.get("time") or row.get("datetime")
    return parse_ist_timestamp(value) if value else None


def _close(row:dict):
    for key in ("close","price","last_price"):
        try:return float(row[key])
        except (KeyError,TypeError,ValueError):pass
    return None


def _snapshot(row:dict|None)->dict|None:
    if row is None:return None
    oi=row.get("open_interest") if "open_interest" in row else row.get("oi")
    return {"timestamp":row.get("timestamp") or row.get("time") or row.get("datetime"),
            "price":_close(row),"volume":row.get("volume"),"open_interest":oi}


def build_reaction_window(event:dict,candles:list[dict],*,as_of:str,
                          immediate_minutes:int=5,confirmation_minutes:int=30,
                          assimilation_minutes:int=60,max_lateness_minutes:int=5,
                          reaction_anchor:str|None=None)->dict:
    """Select strictly point-in-time market snapshots around an event.

    ``as_of`` is mandatory: observations later than it are invisible. A requested
    horizon is also unavailable until its target time has passed. ``reaction_anchor``
    may move horizon timing forward to a known market-session open for news released
    while the market was closed; it can never precede the news event. The next candle
    may satisfy a horizon only within ``max_lateness_minutes`` so unmodelled session
    gaps cannot masquerade as a +5m/+30m reaction. Trade outcomes are never read.
    """
    raw=event.get("available_at") or event.get("published_at")
    if not raw:raise ValueError("event requires available_at or published_at")
    if not as_of:raise ValueError("as_of is required")
    if not (0 < immediate_minutes <= confirmation_minutes <= assimilation_minutes):
        raise ValueError("reaction horizons must satisfy 0 < immediate <= confirmation <= assimilation")
    if max_lateness_minutes < 0:raise ValueError("max_lateness_minutes must be non-negative")

    event_ts=parse_ist_timestamp(raw);as_of_ts=parse_ist_timestamp(as_of)
    anchor_ts=parse_ist_timestamp(reaction_anchor) if reaction_anchor else event_ts
    if anchor_ts < event_ts:raise ValueError("reaction_anchor cannot precede event timestamp")
    base={"event":event,"event_timestamp":raw,"as_of":as_of,"outcome_blind":True,
          "reaction_anchor_timestamp":anchor_ts.isoformat(),
          "reaction_anchor_shift_minutes":(anchor_ts-event_ts).total_seconds()/60.0,
          "horizons_minutes":{"immediate":immediate_minutes,"confirmation":confirmation_minutes,
                              "assimilation":assimilation_minutes}}
    if as_of_ts < event_ts:
        return {**base,"status":"EVENT_NOT_YET_AVAILABLE","pre_event":None,
                "immediate":None,"confirmation":None,"assimilation":None,"horizon_status":{}}

    parsed=[]
    for candle in candles:
        ts=_ts(candle)
        if ts is not None and ts <= as_of_ts:parsed.append((ts,candle))
    rows=sorted(parsed,key=lambda x:x[0])
    # Candle timestamps are observation timestamps. An observation exactly at the
    # event can already contain event-period movement, so pre-event is strict to the
    # news timestamp even when reaction horizons are anchored to a later market open.
    before=[(t,c) for t,c in rows if t < event_ts]
    if not before:
        return {**base,"status":"NO_PRE_EVENT_MARKET","pre_event":None,
                "immediate":None,"confirmation":None,"assimilation":None,"horizon_status":{}}

    tolerance=timedelta(minutes=max_lateness_minutes)
    horizon_status={}
    def observed(name:str,minutes:int):
        target=anchor_ts+timedelta(minutes=minutes)
        if target > as_of_ts:
            horizon_status[name]="NOT_YET_OBSERVABLE";return None
        found=next(((t,c) for t,c in rows if t>=target and t<=target+tolerance),None)
        if found is None:
            horizon_status[name]="NO_OBSERVATION_WITHIN_TOLERANCE";return None
        horizon_status[name]="OBSERVED";return _snapshot(found[1])

    immediate=observed("immediate",immediate_minutes)
    confirmation=observed("confirmation",confirmation_minutes)
    assimilation=observed("assimilation",assimilation_minutes)
    status="READY" if all(x is not None for x in (immediate,confirmation,assimilation)) else "PARTIAL"
    return {**base,"status":status,"pre_event":_snapshot(before[-1][1]),
            "immediate":immediate,"confirmation":confirmation,"assimilation":assimilation,
            "horizon_status":horizon_status,
            "rule":"Only observations visible by as_of and within bounded horizon lateness are eligible; closed-market events may use an explicit later session anchor; no trade outcome is consulted."}
