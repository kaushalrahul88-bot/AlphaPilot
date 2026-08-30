from __future__ import annotations
import json,argparse
from pathlib import Path
def classify(x):
 reasons=[]
 if x.get("provenance_status")!="VERIFIED": reasons.append("PROVENANCE_NOT_VERIFIED")
 if not x.get("available_at"): reasons.append("MISSING_AVAILABLE_AT")
 if not x.get("transmission_channel"): reasons.append("MISSING_TRANSMISSION_CHANNEL")
 effect=x.get("directional_effect","UNKNOWN")
 conf=float(x.get("confidence") or 0)
 if reasons:return "BLOCK",reasons
 if effect in ("MIXED","UNKNOWN") or conf<0.75:return "CONTEXT_ONLY",["NON_UNAMBIGUOUS_CAUSAL_EFFECT"]
 return "ALLOW",["VERIFIED_EXPLICIT_CAUSAL_EFFECT"]
def main(source,output):
 d=json.loads(Path(source).read_text());out=[]
 for x in d.get("events",[]):
  disposition,reasons=classify(x);out.append({**x,"disposition":disposition,"admission_reasons":reasons})
 report={"mode":"COPPER_CAUSAL_EVENT_ADMISSION_V1","events":out,
         "counts":{k:sum(x["disposition"]==k for x in out) for k in ("ALLOW","CONTEXT_ONLY","BLOCK")}}
 Path(output).write_text(json.dumps(report,indent=2,sort_keys=True));print(json.dumps(report,indent=2))
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--source",required=True);p.add_argument("--output",default="causal-event-admission.json");a=p.parse_args();main(a.source,a.output)
