from __future__ import annotations
import argparse,json
from collections import Counter
from datetime import datetime,timedelta
from pathlib import Path
from app.commodity_time import parse_ist_timestamp

def main(source,output):
    d=json.loads(Path(source).read_text())
    rows=d.get("records") or []
    daily=Counter(parse_ist_timestamp(x["available_at"]).date().isoformat() for x in rows if x.get("available_at"))
    accepted=Counter(parse_ist_timestamp(x["available_at"]).date().isoformat() for x in d.get("accepted",[]) if x.get("available_at"))
    report={
      "mode":"COPPER_HISTORICAL_NEWS_COVERAGE_AUDIT_V1",
      "raw_records":len(rows),
      "accepted_records":len(d.get("accepted",[])),
      "days_with_raw_news":len(daily),
      "days_with_accepted_news":len(accepted),
      "raw_by_date":dict(sorted(daily.items())),
      "accepted_by_date":dict(sorted(accepted.items())),
      "coverage_warning":"A zero count does not prove no relevant event occurred; it identifies acquisition/audit coverage gaps requiring additional source acquisition.",
    }
    Path(output).write_text(json.dumps(report,indent=2,sort_keys=True))
    print(json.dumps(report,indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--source",required=True);p.add_argument("--output",default="news-coverage.json");x=p.parse_args()
    main(x.source,x.output)
