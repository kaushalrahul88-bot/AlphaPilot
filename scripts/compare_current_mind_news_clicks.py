from __future__ import annotations
import argparse,json
from pathlib import Path

def load(p): return json.loads(Path(p).read_text())
def action(x): return (x.get("decision") or {}).get("action")
def news(x):
    lanes=((x.get("evidence") or {}).get("lanes") or {}).get("NEWS") or []
    return lanes[0] if lanes else None

def main(baseline,variant,output):
    b=load(baseline);v=load(variant)
    bm={x["click_timestamp"]:x for x in b["decisions"]}; vm={x["click_timestamp"]:x for x in v["decisions"]}
    if set(bm)!=set(vm): raise RuntimeError("Click sets differ")
    changed=[];visible=[]
    for ts in sorted(vm):
        bx,vx=bm[ts],vm[ts]; n=news(vx); detail=(n or {}).get("detail") or {}
        if detail.get("visible"):
            visible.append({"click_timestamp":ts,"baseline_action":action(bx),"news_action":action(vx),
                            "news":n,"baseline_thesis":bx.get("thesis"),"news_thesis":vx.get("thesis"),
                            "baseline_outcome":bx.get("outcome"),"news_outcome":vx.get("outcome")})
        if action(bx)!=action(vx):
            changed.append({"click_timestamp":ts,"baseline_action":action(bx),"news_action":action(vx),
                            "baseline_thesis":bx.get("thesis"),"news_thesis":vx.get("thesis"),
                            "news":n,"baseline_outcome":bx.get("outcome"),"news_outcome":vx.get("outcome"),
                            "baseline_fingerprint":bx.get("decision_fingerprint"),"news_fingerprint":vx.get("decision_fingerprint")})
    report={"mode":"CURRENT_MIND_NEWS_CLICK_FORENSIC_V1",
            "baseline_actions":b.get("actions"),"news_actions":v.get("actions"),
            "baseline_expectancy_r":b.get("expectancy_r_resolved"),"news_expectancy_r":v.get("expectancy_r_resolved"),
            "news_visible_click_count":len(visible),"action_changed_click_count":len(changed),
            "changed_clicks":changed,"news_visible_clicks":visible,
            "rule":"Descriptive forensic audit only; no parameter changes are authorized by this report."}
    Path(output).write_text(json.dumps(report,indent=2,sort_keys=True));print(json.dumps(report,indent=2))

if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--baseline",required=True);p.add_argument("--variant",required=True);p.add_argument("--output",default="news-click-forensic.json");a=p.parse_args();main(a.baseline,a.variant,a.output)
