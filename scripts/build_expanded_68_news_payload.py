from __future__ import annotations
import json
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from app.copper_historical_news_integrity_audit import audit_historical_news_records
from app.copper_news_intelligence import apply_news_intelligence
from scripts.reaudit_frozen_copper_news import _reconstruct_record,EXPECTED_SOURCE_RECORD_COUNT,EXPECTED_SOURCE_DATASET_SHA256

IST=ZoneInfo("Asia/Kolkata")

def next_session_open(date_text):
 d=datetime.fromisoformat(date_text)
 if d.tzinfo is not None:
  d=d.astimezone(IST).replace(tzinfo=None)
 d+=timedelta(days=1)
 while d.weekday()>=5:d+=timedelta(days=1)
 return d.replace(hour=9,minute=0,second=0,microsecond=0,tzinfo=IST).isoformat()

def build(frozen_path,expanded_path,output_path):
 frozen=json.loads(Path(frozen_path).read_text());meta=frozen.get("source_metadata") or {}
 assert frozen["raw_record_count"]==EXPECTED_SOURCE_RECORD_COUNT and meta["dataset_sha256"]==EXPECTED_SOURCE_DATASET_SHA256
 raw=[_reconstruct_record(x) for x in frozen["records"]]
 extra=json.loads(Path(expanded_path).read_text())
 for x in extra["records"]:
  p=x["published_at"]; exact=x["timestamp_precision"]=="EXACT"
  avail=p if exact else next_session_open(p)
  raw.append({"series":"COPPER_NEWS","observed_at":avail,"available_at":avail,"source":x["source"],
   "value":{"headline":x["headline"],"url":x["source_url"],"facts":x.get("facts") or [],
            "timestamp_precision":x["timestamp_precision"],"reported_publication":p},
   "quality":"INDEPENDENT_HISTORICAL_EXACT" if exact else "INDEPENDENT_HISTORICAL_DATE_ONLY_NEXT_SESSION_CAUTION"})
 assert len(raw)==68
 audit=audit_historical_news_records(raw)
 # Preserve the original accepted records (including facts/provenance) for News Intelligence.
 accepted_records=audit.get("accepted_records") or []
 intel=apply_news_intelligence(accepted_records)
 # ALLOW votes directionally. CONTEXT_ONLY is transmitted so later follow-up/context
 # can invalidate or decay an older ALLOW event inside the point-in-time persistence gate.
 replay_records=list(intel["allowed_records"])+list(intel["context_only_records"])
 out={"records":replay_records,"metadata":{"declared_raw_input_count":68,
  "original_frozen_count":54,"independent_unique_count":14,"integrity_classification_counts":audit["classification_counts"],
  "integrity_accepted_count":audit["accepted_record_count"],"news_intelligence_counts":intel["counts"],
  "transmitted_news_record_count":len(replay_records),
  "transmitted_disposition_counts":{"ALLOW":len(intel["allowed_records"]),"CONTEXT_ONLY":len(intel["context_only_records"])},
  "point_in_time":True,"date_only_policy":"NEXT_TRADING_SESSION_OPEN_CAUTION","date_only_session_timezone":"Asia/Kolkata",
  "network_refetch":False}}
 Path(output_path).write_text(json.dumps(out,indent=2))
 print(json.dumps(out["metadata"],indent=2))

if __name__=="__main__":
 import argparse
 p=argparse.ArgumentParser()
 p.add_argument("--source",required=True)
 p.add_argument("--expanded",required=True)
 p.add_argument("--output",required=True)
 a=p.parse_args()
 build(a.source,a.expanded,a.output)
