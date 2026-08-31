from __future__ import annotations
import argparse,json
from collections import Counter
from pathlib import Path
from app.market_news_reaction_windows import build_reaction_window
from app.market_news_reaction_engine import assess_market_news_reaction
from app.market_news_participation import assess_news_participation


def _event(record):
    ni=record.get("news_intelligence") or {}
    value=record.get("value") or {}
    return {"available_at":record.get("available_at") or ni.get("available_at"),
            "stance":ni.get("effect") or value.get("sentiment") or "UNKNOWN",
            "headline":ni.get("headline") or value.get("headline"),
            "source":record.get("source") or ni.get("source"),
            "materiality":ni.get("materiality"),"disposition":ni.get("disposition")}


def audit(news_payload:dict,candles:list[dict])->dict:
    rows=[];reaction_counts=Counter();participation_counts=Counter()
    for raw in news_payload.get("records") or []:
        event=_event(raw)
        try:window=build_reaction_window(event,candles)
        except (TypeError,ValueError) as exc:
            rows.append({"event":event,"status":"INVALID_EVENT_TIMESTAMP","error":str(exc)});continue
        if window.get("status")!="READY":
            rows.append({"event":event,"status":window.get("status")});continue
        reaction=assess_market_news_reaction(event,window.get("pre_event"),window.get("immediate"),
                                             window.get("confirmation"),window.get("assimilation"))
        participation=assess_news_participation(window,reaction)
        reaction_counts[reaction["reaction_state"]]+=1
        participation_counts[participation["participation_state"]]+=1
        rows.append({"event":event,"status":"CLASSIFIED","window":window,"reaction":reaction,
                     "participation":participation})
    return {"mode":"MARKET_NEWS_REACTION_AUDIT_V1","outcome_blind":True,"outcomes_read":False,
            "events":len(news_payload.get("records") or []),"classified":sum(reaction_counts.values()),
            "reaction_counts":dict(sorted(reaction_counts.items())),
            "participation_counts":dict(sorted(participation_counts.items())),"records":rows,
            "guardrails":["Trade outcomes and P&L are not accepted as audit inputs.",
                          "News and candles must be frozen point-in-time inputs.",
                          "Missing market observations remain unclassified.",
                          "This report describes market assimilation; it does not generate trades."]}


def main():
    p=argparse.ArgumentParser();p.add_argument("--news",required=True);p.add_argument("--candles",required=True);p.add_argument("--output")
    a=p.parse_args();news=json.loads(Path(a.news).read_text());candles=json.loads(Path(a.candles).read_text())
    if isinstance(candles,dict):candles=candles.get("candles") or candles.get("records") or []
    result=audit(news,candles);text=json.dumps(result,indent=2,sort_keys=True)
    if a.output:Path(a.output).write_text(text+"\n")
    else:print(text)

if __name__=="__main__":main()
