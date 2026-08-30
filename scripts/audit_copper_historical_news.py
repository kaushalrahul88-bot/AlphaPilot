from __future__ import annotations
import argparse, asyncio, json
from app.copper_historical_news import fetch_copper_historical_news
from app.copper_historical_news_integrity_audit import audit_historical_news_records
from app.copper_market_brain_direction_audit import PRIMARY_START, PRIMARY_END

async def main(path: str):
    fetched=await fetch_copper_historical_news(PRIMARY_START,PRIMARY_END)
    audit=audit_historical_news_records(fetched.get("records") or [])
    audit["source_metadata"]={k:v for k,v in fetched.items() if k!="records"}
    with open(path,"w",encoding="utf-8") as f:
        json.dump(audit,f,ensure_ascii=False,indent=2,sort_keys=True)
    print(f"Wrote {path}: raw={audit['raw_record_count']} accepted={audit['accepted_record_count']} sha256={audit['accepted_dataset_sha256']}")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--output",default="news-audit.json")
    args=p.parse_args()
    asyncio.run(main(args.output))
