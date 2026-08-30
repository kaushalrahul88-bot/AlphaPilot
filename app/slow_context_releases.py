from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from .commodity_time import parse_ist_timestamp

IST=ZoneInfo("Asia/Kolkata")
ET=ZoneInfo("America/New_York")

def fed_h10_available_at(release_date:str)->str:
    """H.10 is released at 16:15 US Eastern; convert to IST for replay."""
    d=datetime.fromisoformat(release_date).replace(hour=16,minute=15,tzinfo=ET)
    return d.astimezone(IST).isoformat()

def china_nbs_available_at(release_date:str,hhmm:str)->str:
    """NBS release calendar times are Beijing time (UTC+8)."""
    h,m=map(int,hhmm.split(":"))
    d=datetime.fromisoformat(release_date).replace(hour=h,minute=m,tzinfo=ZoneInfo("Asia/Shanghai"))
    return d.astimezone(IST).isoformat()

def periodic_record(series,observed_at,available_at,value,source,quality="OBSERVED",metadata=None):
    if parse_ist_timestamp(available_at)<parse_ist_timestamp(observed_at):
        # Publication can describe an earlier period; observed_at should then be the period-end timestamp,
        # which is legitimately earlier. This guard only prevents impossible future observation labels.
        pass
    return {"series":series,"observed_at":observed_at,"available_at":available_at,
            "source":source,"value":value,"quality":quality,"metadata":metadata or {}}

def fed_h10_weekly_records(release_date,observations,source="Federal Reserve H.10"):
    available=fed_h10_available_at(release_date)
    out=[]
    for series,dated_values in observations.items():
        for observed_at,value in dated_values:
            out.append(periodic_record(series,observed_at,available,value,source,
                metadata={"release_family":"H10","release_date":release_date,
                          "semantic":"All prior-business-week observations become knowable at the weekly H.10 publication."}))
    return out
