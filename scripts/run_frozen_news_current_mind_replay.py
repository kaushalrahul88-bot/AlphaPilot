from __future__ import annotations
import argparse, asyncio, json
from pathlib import Path
from app.commodity_store import CommodityStore
from app.copper_news_intelligence import apply_news_intelligence
from app.current_mind_copper_replay import run_current_mind_news_replay_from_store

def _record(row):
    return {
      "series":"COPPER_NEWS","observed_at":row["available_at"],"available_at":row["available_at"],
      "source":row.get("source") or "GDELT",
      "value":{"headline":row["headline"],"sentiment":row.get("raw_sentiment"),"url":row.get("url")},
      "quality":"FROZEN_INTEGRITY_AUDIT_ACCEPTED",
    }

async def main(source,output):
    d=json.loads(Path(source).read_text())
    records=[_record(x) for x in d.get("accepted",[])]
    intel=apply_news_intelligence(records)
    allowed=intel["allowed_records"]
    if len(allowed)!=1:raise RuntimeError(f"Expected exactly one News Intelligence ALLOW record, got {len(allowed)}")
    if "congo" not in allowed[0]["value"]["headline"].lower():raise RuntimeError("Unexpected allowed headline")
    store=CommodityStore()
    report=await run_current_mind_news_replay_from_store(store,allowed,{
      "source_dataset_sha256":(d.get("source_metadata") or {}).get("source_dataset_sha256"),
      "accepted_dataset_sha256":d.get("accepted_dataset_sha256"),
      "integrity_classification_counts":d.get("classification_counts"),
      "news_intelligence_counts":intel.get("counts"),
      "news_intelligence_policy":intel.get("policy"),
      "allowed_headlines":[x["value"]["headline"] for x in allowed],
    })
    Path(output).write_text(json.dumps(report,indent=2,sort_keys=True))
    print(json.dumps({k:report.get(k) for k in ("actions","targets","stops","no_entry","session_end","expectancy_r_resolved","missed_large_moves_after_abstention","news_metadata")},indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--source",required=True);p.add_argument("--output",default="news-replay.json");a=p.parse_args()
    asyncio.run(main(a.source,a.output))
