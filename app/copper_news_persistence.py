from __future__ import annotations
from datetime import datetime,timedelta
from app.commodity_time import parse_ist_timestamp

PERSISTENCE={
 "SUPPLY":{"max_days":5,"decay_hours":24},
 "DEMAND":{"max_days":3,"decay_hours":18},
 "MACRO":{"max_days":2,"decay_hours":12},
}
INVALIDATION_TERMS=("reversed","lifted","waiver","waivers","restart","restarted","reopens","resolved","ended","cancelled","canceled","restored","recovered")

def assess_news_persistence(record:dict,click,subsequent_records=None)->dict:
 ni=record.get("news_intelligence") or {}
 ts=parse_ist_timestamp(record["available_at"]); click=parse_ist_timestamp(click) if isinstance(click,str) else click
 age=max(0.0,(click-ts).total_seconds()/3600)
 mechanism=ni.get("transmission_mechanism") or "MACRO"; cfg=PERSISTENCE.get(mechanism,PERSISTENCE["MACRO"])
 headline=((record.get("value") or {}).get("headline") or "").lower()
 invalidators=[]
 for newer in subsequent_records or []:
  nts=parse_ist_timestamp(newer["available_at"])
  if not(ts<nts<=click):continue
  n=((newer.get("value") or {}).get("headline") or "").lower()
  # Conservative invalidation: require overlap with a named subject/entity plus explicit resolution language.
  entities=[x for x in ("congo","drc","codelco","escondida","grasberg","collahuasi","las bambas","kamoa","tenke","chuquicamata") if x in headline]
  if entities and any(x in n for x in entities) and any(t in n for t in INVALIDATION_TERMS):invalidators.append(newer)
 if invalidators:
  return {"status":"STALE_INVALIDATED","weight":0.0,"age_hours":round(age,2),"mechanism":mechanism,"reason":"SUBSEQUENT_EVENT_INVALIDATES_OR_RESOLVES"}
 maxh=cfg["max_days"]*24
 if age>maxh:return {"status":"STALE_EXPIRED","weight":0.0,"age_hours":round(age,2),"mechanism":mechanism,"reason":"MECHANISM_MAX_PERSISTENCE_EXCEEDED"}
 weight=max(0.25,1.0-age/(maxh*1.25))
 return {"status":"ACTIVE" if age<=cfg["decay_hours"] else "ACTIVE_DECAYED","weight":round(weight,3),"age_hours":round(age,2),"mechanism":mechanism,"reason":"CAUSAL_EFFECT_STILL_WITHIN_MECHANISM_HORIZON"}
