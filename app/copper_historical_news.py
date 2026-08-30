from __future__ import annotations
import asyncio, hashlib, json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import httpx
from .commodity_time import parse_ist_timestamp
from .news import _commodity_sentiment, _event_tags

GDELT_DOC="https://api.gdeltproject.org/api/v2/doc/doc"
QUERY='(copper OR "copper prices" OR "copper mine" OR "copper demand" OR "copper inventories" OR "copper smelter" OR "COMEX copper" OR "LME copper")'
MAX_PER_DAY=250

def _seen(value):
    s=str(value or "").strip()
    for fmt in ("%Y%m%dT%H%M%SZ","%Y%m%d%H%M%S"):
        try:return datetime.strptime(s,fmt).replace(tzinfo=timezone.utc)
        except ValueError:pass
    try:return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
    except Exception:return None

def _domain(url):
    try:return urlparse(str(url or "")).netloc.lower().removeprefix("www.")
    except Exception:return ""

def _relevant(title):
    # Broad retrieval only. Final trading relevance is decided by the separate
    # integrity audit; retrieval must not silently discard records before review.
    t=str(title or "").lower()
    return "copper" in t or ("lme" in t and "metal" in t) or ("comex" in t and "metal" in t)

async def _fetch_day(client, start, end):
    params={"query":QUERY,"mode":"artlist","format":"json","maxrecords":MAX_PER_DAY,
            "sort":"DateAsc","startdatetime":start.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "enddatetime":end.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")}
    r=await client.get(GDELT_DOC,params=params);r.raise_for_status()
    data=r.json()
    return data.get("articles") or []

async def fetch_copper_historical_news(start, end):
    """Fetch genuine timestamped historical news. GDELT seendate is used as conservative available_at."""
    days=[];cur=start
    while cur<end:
        nxt=min(end,cur+timedelta(days=1));days.append((cur,nxt));cur=nxt
    headers={"User-Agent":"AlphaPilot/1.0 historical-news-research"}
    async with httpx.AsyncClient(timeout=45,follow_redirects=True,headers=headers) as client:
        raw=[]
        for i in range(0,len(days),3):
            batch=await asyncio.gather(*(_fetch_day(client,a,b) for a,b in days[i:i+3]))
            for rows in batch:raw.extend(rows)
            if i+3<len(days):await asyncio.sleep(1.0)
    dedup={}
    for a in raw:
        title=str(a.get("title") or "").strip();url=str(a.get("url") or "").strip();seen=_seen(a.get("seendate"))
        if not title or not seen or not _relevant(title):continue
        key=(url or title.lower(),seen.isoformat())
        dedup[key]={"series":"COPPER_NEWS","observed_at":seen.isoformat(),"available_at":seen.isoformat(),
          "source":_domain(url) or "GDELT","value":{"headline":title,"url":url or None,"domain":_domain(url),
          "language":a.get("language"),"sourcecountry":a.get("sourcecountry"),
          "sentiment":_commodity_sentiment("COPPER",title),"event_tags":_event_tags("COPPER",title),
          "gdelt_seendate":a.get("seendate")},"quality":"GDELT_SEEN_TIMESTAMP"}
    records=sorted(dedup.values(),key=lambda x:parse_ist_timestamp(x["available_at"]))
    digest=hashlib.sha256(json.dumps(records,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return {"provider":"GDELT DOC 2.0","query":QUERY,"records":records,"record_count":len(records),
            "dataset_sha256":digest,"timestamp_semantics":"available_at = GDELT seendate; no article is visible before GDELT observed it.",
            "retrieved_at":datetime.now(timezone.utc).isoformat()}
