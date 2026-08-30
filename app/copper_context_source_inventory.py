from __future__ import annotations
from .copper_point_in_time_context import SERIES_POLICY

SOURCE_INVENTORY={
 "MCX_COPPER":{"availability":"AVAILABLE","granularity":"5m","point_in_time":True,
   "source":"AlphaPilot stored COPPER31AUG26FUT","action":"use as primary replay clock"},
 "COMEX_HG":{"availability":"ENTITLEMENT_REQUIRED","granularity":"intraday required","point_in_time":True,
   "source":"CME DataMine / licensed CME source","action":"acquire if entitled; otherwise mark unavailable"},
 "LME_COPPER":{"availability":"ENTITLEMENT_REQUIRED","granularity":"intraday or timestamped official observation","point_in_time":True,
   "source":"LME / licensed distributor","action":"acquire if timestamp-safe"},
 "USDINR":{"availability":"SOURCE_REQUIRED","granularity":"intraday preferred","point_in_time":True,
   "source":"validated historical FX source","action":"acquire timestamped series; periodic official fix may be separate report context"},
 "DXY":{"availability":"SOURCE_REQUIRED","granularity":"intraday preferred","point_in_time":True,
   "source":"validated historical market-data source","action":"acquire timestamped series; daily index is report context only"},
 "USDCNY":{"availability":"SOURCE_REQUIRED","granularity":"intraday preferred","point_in_time":True,
   "source":"validated historical FX source","action":"acquire timestamped series"},
 "COPPER_NEWS":{"availability":"ARCHIVE_REQUIRED","granularity":"publication timestamp","point_in_time":True,
   "source":"reputable timestamped news archive","action":"store original publication availability; preserve updates separately"},
 "MACRO_RELEASE":{"availability":"PUBLIC_ARCHIVE_CANDIDATE","granularity":"release/event","point_in_time":True,
   "source":"official statistical/central-bank release archive where possible","action":"store release time and latest-known value"},
 "MCX_COPPER_OPTION":{"availability":"FORWARD_COLLECTION","granularity":"snapshot/candle","point_in_time":True,
   "source":"AlphaPilot collector","action":"use only genuinely collected history; never synthesize missing premium"},
}

def source_inventory():
    missing=set(SERIES_POLICY)-set(SOURCE_INVENTORY)
    if missing: raise RuntimeError(f"Missing inventory policy: {sorted(missing)}")
    return {"mode":"COPPER_CONTEXT_SOURCE_INVENTORY_V1","feeds":SOURCE_INVENTORY,
      "replay_gate":"No external feed enters Current-Mind Replay until point-in-time availability is proven.",
      "priority":["MCX_COPPER","COMEX_HG","USDINR","DXY","USDCNY","LME_COPPER","MACRO_RELEASE","COPPER_NEWS","MCX_COPPER_OPTION"]}
