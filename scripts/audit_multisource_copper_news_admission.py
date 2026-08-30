from __future__ import annotations
import argparse,json
from pathlib import Path
from app.copper_historical_news_integrity_audit import audit_historical_news_records
from app.copper_news_intelligence import apply_news_intelligence

def record(x):
 return {"series":"COPPER_NEWS","available_at":x["published_at"],"observed_at":x["published_at"],
         "source":x["source"],"value":{"headline":x["fact"],"url":x["url"],"language":"English"},
         "quality":"MULTISOURCE_CANDIDATE"}

def main(source,output):
 d=json.loads(Path(source).read_text()); candidates=d["candidates"]
 primary=[x for x in candidates if not x["initial_label"].startswith("OUTSIDE_")]
 rows=[record(x) for x in primary]
 integrity=audit_historical_news_records(rows)
 intel=apply_news_intelligence(integrity.get("accepted_records") or [])
 report={"mode":"COPPER_HISTORICAL_NEWS_ADMISSION_AUDIT_V1",
  "candidate_count":len(candidates),"primary_window_candidate_count":len(primary),
  "integrity_counts":integrity.get("classification_counts"),
  "integrity_accepted_count":len(integrity.get("accepted_records") or []),
  "news_intelligence_counts":intel.get("counts"),
  "allowed_records":intel.get("allowed_records") or [],
  "outside_primary_window":[x for x in candidates if x["initial_label"].startswith("OUTSIDE_")],
  "policy":"Candidate ledger remains quarantined; this report is classification evidence only and does not feed Market Brain."}
 Path(output).write_text(json.dumps(report,indent=2,sort_keys=True));print(json.dumps(report,indent=2))
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--source",default="research/copper_historical_news_candidate_ledger_v1.json");p.add_argument("--output",default="historical-news-admission-v1.json");a=p.parse_args();main(a.source,a.output)
