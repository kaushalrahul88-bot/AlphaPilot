from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from statistics import median

from .commodity_time import parse_ist_timestamp


def _ts(row:dict):
    value=row.get("timestamp") or row.get("time") or row.get("datetime")
    return parse_ist_timestamp(value) if value else None


def _number(row:dict|None,key:str):
    if not isinstance(row,dict):return None
    try:return float(row[key]) if row.get(key) is not None else None
    except (TypeError,ValueError):return None


def _close(row:dict):
    for key in ("close","price","last_price"):
        value=_number(row,key)
        if value is not None:return value
    return None


def infer_volume_semantics(candles:list[dict],*,min_sessions:int=3,min_points_per_session:int=12)->dict:
    """Infer whether raw volume behaves like a session-cumulative field."""
    if min_sessions < 1 or min_points_per_session < 2:
        raise ValueError("volume inference requires min_sessions>=1 and min_points_per_session>=2")
    grouped=defaultdict(list)
    for candle in candles:
        ts=_ts(candle);volume=_number(candle,"volume")
        if ts is not None and volume is not None:grouped[ts.date()].append((ts,volume))
    qualifying=0;nondecreasing=0
    for values in grouped.values():
        values=sorted(values,key=lambda x:x[0])
        if len(values)<min_points_per_session:continue
        qualifying+=1
        if all(values[i][1]>=values[i-1][1] for i in range(1,len(values))):nondecreasing+=1
    cumulative=qualifying>=min_sessions and nondecreasing==qualifying
    return {"mode":"SESSION_CUMULATIVE_INFERRED" if cumulative else "UNKNOWN",
            "sessions_seen":len(grouped),"qualifying_sessions":qualifying,
            "nondecreasing_sessions":nondecreasing,"min_sessions":min_sessions,
            "min_points_per_session":min_points_per_session,"outcome_blind":True,
            "rule":"Session-cumulative volume is inferred only when every sufficiently populated checked session is nondecreasing; otherwise volume semantics remain unknown."}


def _snapshot(row:dict|None,*,bar_volume:float|None=None,volume_semantics:str="UNKNOWN")->dict|None:
    if row is None:return None
    oi=row.get("open_interest") if "open_interest" in row else row.get("oi")
    return {"timestamp":row.get("timestamp") or row.get("time") or row.get("datetime"),
            "price":_close(row),"volume":row.get("volume"),"bar_volume":bar_volume,
            "volume_semantics":volume_semantics,"open_interest":oi}


def _rows_with_bar_volume(rows:list[tuple],volume_semantics:str)->list[tuple]:
    enriched=[];previous_by_day={}
    for ts,row in rows:
        raw=_number(row,"volume");bar_volume=None
        if volume_semantics=="SESSION_CUMULATIVE_INFERRED" and raw is not None:
            previous=previous_by_day.get(ts.date())
            if previous is None:bar_volume=raw
            elif raw>=previous:bar_volume=raw-previous
            previous_by_day[ts.date()]=raw
        enriched.append((ts,row,bar_volume))
    return enriched


def _empty_volume_activity(volume_semantics:str,requested:int,minimum:int)->dict:
    return {"semantics":volume_semantics,"baseline_method":"PRIOR_SESSION_MATCHED_CLOCK_SLOT",
            "baseline_sessions_requested":requested,"baseline_min_sessions":minimum,
            "baseline_sessions_used":0,"baseline_clock_slot":None,"median_bar_volume":None}


def build_reaction_window(event:dict,candles:list[dict],*,as_of:str,
                          immediate_minutes:int=5,confirmation_minutes:int=30,
                          assimilation_minutes:int=60,max_lateness_minutes:int=5,
                          reaction_anchor:str|None=None,volume_semantics:str="UNKNOWN",
                          volume_baseline_sessions:int=5,volume_baseline_min_sessions:int=3)->dict:
    """Select strictly point-in-time market snapshots around an event.

    Closed-market events may anchor horizons to the next market open. If raw volume
    is session-cumulative, it is converted to per-bar activity. Immediate activity is
    compared only with the same clock slot from prior sessions, avoiding the strong
    intraday seasonality distortion that occurs when an opening bar is compared with
    the previous session's closing hour. All baseline observations must predate the
    news event. Trade outcomes are never read.
    """
    raw=event.get("available_at") or event.get("published_at")
    if not raw:raise ValueError("event requires available_at or published_at")
    if not as_of:raise ValueError("as_of is required")
    if not (0 < immediate_minutes <= confirmation_minutes <= assimilation_minutes):
        raise ValueError("reaction horizons must satisfy 0 < immediate <= confirmation <= assimilation")
    if max_lateness_minutes < 0:raise ValueError("max_lateness_minutes must be non-negative")
    if volume_baseline_sessions < 1:raise ValueError("volume_baseline_sessions must be positive")
    if not (1 <= volume_baseline_min_sessions <= volume_baseline_sessions):
        raise ValueError("volume baseline requires 1 <= minimum <= requested sessions")

    event_ts=parse_ist_timestamp(raw);as_of_ts=parse_ist_timestamp(as_of)
    anchor_ts=parse_ist_timestamp(reaction_anchor) if reaction_anchor else event_ts
    if anchor_ts < event_ts:raise ValueError("reaction_anchor cannot precede event timestamp")
    empty_activity=_empty_volume_activity(volume_semantics,volume_baseline_sessions,volume_baseline_min_sessions)
    base={"event":event,"event_timestamp":raw,"as_of":as_of,"outcome_blind":True,
          "reaction_anchor_timestamp":anchor_ts.isoformat(),
          "reaction_anchor_shift_minutes":(anchor_ts-event_ts).total_seconds()/60.0,
          "horizons_minutes":{"immediate":immediate_minutes,"confirmation":confirmation_minutes,
                              "assimilation":assimilation_minutes}}
    if as_of_ts < event_ts:
        return {**base,"status":"EVENT_NOT_YET_AVAILABLE","pre_event":None,
                "immediate":None,"confirmation":None,"assimilation":None,"horizon_status":{},
                "volume_activity":empty_activity}

    parsed=[]
    for candle in candles:
        ts=_ts(candle)
        if ts is not None and ts <= as_of_ts:parsed.append((ts,candle))
    rows=_rows_with_bar_volume(sorted(parsed,key=lambda x:x[0]),volume_semantics)
    before=[row for row in rows if row[0] < event_ts]
    if not before:
        return {**base,"status":"NO_PRE_EVENT_MARKET","pre_event":None,
                "immediate":None,"confirmation":None,"assimilation":None,"horizon_status":{},
                "volume_activity":empty_activity}

    tolerance=timedelta(minutes=max_lateness_minutes)
    horizon_status={}
    def observed(name:str,minutes:int):
        target=anchor_ts+timedelta(minutes=minutes)
        if target > as_of_ts:
            horizon_status[name]="NOT_YET_OBSERVABLE";return None
        found=next((row for row in rows if row[0]>=target and row[0]<=target+tolerance),None)
        if found is None:
            horizon_status[name]="NO_OBSERVATION_WITHIN_TOLERANCE";return None
        horizon_status[name]="OBSERVED";return _snapshot(found[1],bar_volume=found[2],volume_semantics=volume_semantics)

    immediate=observed("immediate",immediate_minutes)
    confirmation=observed("confirmation",confirmation_minutes)
    assimilation=observed("assimilation",assimilation_minutes)

    volume_activity=dict(empty_activity)
    if immediate is not None and immediate.get("bar_volume") is not None:
        immediate_ts=parse_ist_timestamp(immediate["timestamp"])
        clock_slot=immediate_ts.time()
        candidates=[(ts,bar_volume) for ts,_,bar_volume in before
                    if bar_volume is not None and ts.time()==clock_slot]
        candidates=candidates[-volume_baseline_sessions:]
        values=[value for _,value in candidates]
        volume_activity.update({"baseline_sessions_used":len(values),
                                "baseline_clock_slot":clock_slot.isoformat(),
                                "baseline_dates":[ts.date().isoformat() for ts,_ in candidates],
                                "median_bar_volume":median(values) if len(values)>=volume_baseline_min_sessions else None})

    status="READY" if all(x is not None for x in (immediate,confirmation,assimilation)) else "PARTIAL"
    return {**base,"status":status,
            "pre_event":_snapshot(before[-1][1],bar_volume=before[-1][2],volume_semantics=volume_semantics),
            "immediate":immediate,"confirmation":confirmation,"assimilation":assimilation,
            "horizon_status":horizon_status,"volume_activity":volume_activity,
            "rule":"Only observations visible by as_of and within bounded horizon lateness are eligible; closed-market events may use an explicit later session anchor; cumulative volume is normalized only after explicit semantics inference and compared with prior-session activity at the same clock slot; no trade outcome is consulted."}
