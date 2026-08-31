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
    return {"timestamp":row.get("timestamp") or row.get("time") or row.get("datetime"),
            "price":_close(row),"volume":row.get("volume"),"open_interest":row.get("open_interest") or row.get("oi")}


def build_reaction_window(event:dict,candles:list[dict],*,immediate_minutes:int=5,
                          confirmation_minutes:int=30,assimilation_minutes:int=60)->dict:
    """Select causal market snapshots around an event from already-frozen candles.

    The event timestamp is when the information became available, not when it was
    later collected. Selection is deterministic and contains no trade outcomes.
    """
    raw=event.get("available_at") or event.get("published_at")
    if not raw:raise ValueError("event requires available_at or published_at")
    event_ts=parse_ist_timestamp(raw)
    rows=sorted((( _ts(c),c) for c in candles if _ts(c) is not None),key=lambda x:x[0])
    before=[(t,c) for t,c in rows if t<=event_ts]
    if not before:
        return {"event":event,"event_timestamp":raw,"status":"NO_PRE_EVENT_MARKET","pre_event":None,
                "immediate":None,"confirmation":None,"assimilation":None,"outcome_blind":True}

    def at_or_after(minutes:int):
        target=event_ts+timedelta(minutes=minutes)
        return next((c for t,c in rows if t>=target),None)

    return {"event":event,"event_timestamp":raw,"status":"READY","outcome_blind":True,
            "pre_event":_snapshot(before[-1][1]),
            "immediate":_snapshot(at_or_after(immediate_minutes)),
            "confirmation":_snapshot(at_or_after(confirmation_minutes)),
            "assimilation":_snapshot(at_or_after(assimilation_minutes)),
            "horizons_minutes":{"immediate":immediate_minutes,"confirmation":confirmation_minutes,
                                "assimilation":assimilation_minutes},
            "rule":"Snapshots are selected only from frozen market observations at or after fixed causal horizons; no trade outcome is consulted."}
