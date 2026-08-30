from __future__ import annotations
from dataclasses import dataclass,asdict
from datetime import datetime
from typing import Any
from .commodity_time import parse_ist_timestamp

@dataclass(frozen=True)
class PointInTimeContextRecord:
    series:str
    observed_at:str
    available_at:str
    source:str
    value:Any
    quality:str="OBSERVED"
    metadata:dict|None=None

    def to_dict(self): return asdict(self)

SERIES_POLICY={
 "MCX_COPPER":{"role":"PRIMARY_UNDERLYING","required":True},
 "COMEX_HG":{"role":"GLOBAL_COPPER_CONFIRMATION","required":False},
 "LME_COPPER":{"role":"GLOBAL_REFERENCE","required":False},
 "USDINR":{"role":"MCX_CURRENCY_TRANSLATION","required":False},
 "DXY":{"role":"USD_REGIME","required":False},
 "USDCNY":{"role":"CHINA_CURRENCY_CONTEXT","required":False},
 "COPPER_NEWS":{"role":"EVENT_CONTEXT","required":False},
 "MACRO_RELEASE":{"role":"MACRO_CONTEXT","required":False},
 "MCX_COPPER_OPTION":{"role":"OPTION_TRANSLATION","required":False},
}

def visible_at(records,click_timestamp):
    click=parse_ist_timestamp(click_timestamp)
    out=[]
    for record in records:
        available=parse_ist_timestamp(record["available_at"])
        observed=parse_ist_timestamp(record["observed_at"])
        if available<=click and observed<=click:
            out.append(record)
    return sorted(out,key=lambda x:(x["series"],x["observed_at"],x["available_at"]))

def audit_context_coverage(records,click_timestamps):
    rows=[]
    for ts in click_timestamps:
        visible=visible_at(records,ts)
        series={x["series"] for x in visible}
        rows.append({"click_timestamp":ts,"visible_series":sorted(series),
                     "missing_series":sorted(set(SERIES_POLICY)-series)})
    return {"mode":"COPPER_POINT_IN_TIME_CONTEXT_COVERAGE_V1",
            "lookahead_guard":"observed_at <= click AND available_at <= click",
            "series_policy":SERIES_POLICY,"clicks":rows}

def acquisition_manifest():
    return {
      "mode":"COPPER_CONTEXT_ACQUISITION_MANIFEST_V1",
      "principle":"Unavailable historical context stays unavailable; never backfill a simulated click with information published later.",
      "feeds":[
        {"series":"MCX_COPPER","status":"AVAILABLE_INTERNAL","source":"existing stored COPPER31AUG26FUT 5m candles"},
        {"series":"COMEX_HG","status":"CME_DATAMINE_ENTITLEMENT_REQUIRED","preferred_source":"CME DataMine","access_note":"Authenticated API downloads only files already purchased/entitled; do not substitute daily web quotes for intraday point-in-time replay."},
        {"series":"LME_COPPER","status":"ENTITLEMENT_OR_VENDOR_REQUIRED","preferred_source":"LME historical data / licensed distributor"},
        {"series":"USDINR","status":"INTRADAY_SOURCE_REQUIRED","requirement":"timestamped intraday observations; daily RBI/Fed reference rates are context-only and cannot stand in for click-time FX"},
        {"series":"DXY","status":"INTRADAY_SOURCE_REQUIRED","requirement":"timestamped intraday observations; daily Federal Reserve dollar indexes are context-only"},
        {"series":"USDCNY","status":"SOURCE_TO_VALIDATE","requirement":"timestamped intraday observations"},
        {"series":"COPPER_NEWS","status":"SOURCE_TO_VALIDATE","requirement":"publication timestamp plus source and revision-safe text/event metadata"},
        {"series":"MACRO_RELEASE","status":"SOURCE_TO_VALIDATE","requirement":"release timestamp, actual, consensus when available, prior and revision metadata"},
        {"series":"MCX_COPPER_OPTION","status":"COLLECT_FORWARD","source":"AlphaPilot option snapshot collector; do not infer unavailable history"},
      ],
      "required_fields":["series","observed_at","available_at","source","value","quality"],
    }
