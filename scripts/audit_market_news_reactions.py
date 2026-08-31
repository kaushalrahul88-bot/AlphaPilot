from __future__ import annotations
import argparse,json
from collections import Counter
from pathlib import Path
from app.market_news_reaction_windows import build_reaction_window
from app.market_news_reaction_engine import assess_market_news_reaction
from app.market_news_observed_path import assess_observed_market_path
from app.market_news_participation import assess_news_participation
from app.commodity_time import parse_ist_timestamp


def _event(record):
    ni=record.get("news_intelligence") or {}
    value=record.get("value") or {}
    return {"available_at":record.get("available_at") or ni.get("available_at"),
            "stance":ni.get("effect") or value.get("sentiment") or "UNKNOWN",
            "headline":ni.get("headline") or value.get("headline"),
            "source":record.get("source") or ni.get("source"),
            "materiality":ni.get("materiality"),"disposition":ni.get("disposition")}


def _market_coverage(candles:list[dict],*,as_of_ts)->dict:
    timestamps=[]
    for candle in candles:
        raw=candle.get("timestamp") or candle.get("time") or candle.get("datetime")
        if not raw:continue
        try:ts=parse_ist_timestamp(raw)
        except (TypeError,ValueError):continue
        if ts<=as_of_ts:timestamps.append((ts,raw))
    if not timestamps:
        return {"status":"NO_MARKET_DATA","first_timestamp":None,"last_timestamp":None,"observations":0}
    timestamps.sort(key=lambda x:x[0])
    return {"status":"AVAILABLE","first_timestamp":timestamps[0][1],"last_timestamp":timestamps[-1][1],
            "observations":len(timestamps)}


def audit(news_payload:dict,candles:list[dict],*,as_of:str)->dict:
    rows=[];reaction_counts=Counter();observed_path_counts=Counter();participation_counts=Counter();coverage_counts=Counter()
    as_of_ts=parse_ist_timestamp(as_of)
    coverage=_market_coverage(candles,as_of_ts=as_of_ts)
    coverage_end=parse_ist_timestamp(coverage["last_timestamp"]) if coverage["last_timestamp"] else None
    for raw in news_payload.get("records") or []:
        event=_event(raw);event_raw=event.get("available_at") or event.get("published_at")
        try:event_ts=parse_ist_timestamp(event_raw) if event_raw else None
        except (TypeError,ValueError):event_ts=None
        if event_ts is not None and coverage_end is not None and event_ts > coverage_end:
            coverage_counts["OUTSIDE_CANDLE_COVERAGE"]+=1
            rows.append({"event":event,"status":"OUTSIDE_CANDLE_COVERAGE","coverage_status":"OUTSIDE_CANDLE_COVERAGE",
                         "market_coverage":coverage});continue
        try:window=build_reaction_window(event,candles,as_of=as_of)
        except (TypeError,ValueError) as exc:
            coverage_counts["INVALID_EVENT_TIMESTAMP"]+=1
            rows.append({"event":event,"status":"INVALID_EVENT_TIMESTAMP","coverage_status":"INVALID_EVENT_TIMESTAMP",
                         "error":str(exc)});continue
        if window.get("status")!="READY":
            coverage_counts["INSUFFICIENT_REACTION_WINDOW"]+=1
            rows.append({"event":event,"status":window.get("status"),"coverage_status":"INSUFFICIENT_REACTION_WINDOW",
                         "window":window});continue
        coverage_counts["CLASSIFIABLE"]+=1
        observed_path=assess_observed_market_path(window.get("pre_event"),window.get("immediate"),
                                                  window.get("confirmation"),window.get("assimilation"))
        reaction=assess_market_news_reaction(event,window.get("pre_event"),window.get("immediate"),
                                             window.get("confirmation"),window.get("assimilation"))
        participation=assess_news_participation(window,reaction)
        observed_path_counts[observed_path["path_state"]]+=1
        reaction_counts[reaction["reaction_state"]]+=1;participation_counts[participation["participation_state"]]+=1
        rows.append({"event":event,"status":"CLASSIFIED","coverage_status":"CLASSIFIABLE","window":window,
                     "observed_path":observed_path,"reaction":reaction,"participation":participation})
    return {"mode":"MARKET_NEWS_REACTION_AUDIT_V1","outcome_blind":True,"outcomes_read":False,"as_of":as_of,
            "events":len(news_payload.get("records") or []),"market_coverage":coverage,
            "coverage_counts":dict(sorted(coverage_counts.items())),"classified":sum(reaction_counts.values()),
            "observed_path_counts":dict(sorted(observed_path_counts.items())),
            "reaction_counts":dict(sorted(reaction_counts.items())),
            "participation_counts":dict(sorted(participation_counts.items())),"records":rows,
            "guardrails":["Trade outcomes and P&L are not accepted as audit inputs.",
                          "News and candles must be frozen point-in-time inputs.",
                          "Observed market path is separated from directional news-hypothesis confirmation.",
                          "Unknown news stance cannot erase an otherwise observable market price path.",
                          "Event coverage is reported separately from reaction classification.",
                          "Events later than frozen candle coverage cannot be classified.",
                          "Market coverage itself is also clipped to the explicit as_of cutoff.",
                          "No market observation later than the explicit as_of cutoff is visible.",
                          "Missing or late market observations remain unclassified.",
                          "This report describes market assimilation; it does not generate trades."]}


def main():
    p=argparse.ArgumentParser();p.add_argument("--news",required=True);p.add_argument("--candles",required=True)
    p.add_argument("--as-of",required=True);p.add_argument("--output")
    a=p.parse_args();news=json.loads(Path(a.news).read_text());candles=json.loads(Path(a.candles).read_text())
    if isinstance(candles,dict):candles=candles.get("candles") or candles.get("records") or []
    result=audit(news,candles,as_of=a.as_of);text=json.dumps(result,indent=2,sort_keys=True)
    if a.output:Path(a.output).write_text(text+"\n")
    else:print(text)

if __name__=="__main__":main()
