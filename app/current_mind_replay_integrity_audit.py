from __future__ import annotations
from collections import Counter
from statistics import mean
from .commodity_time import parse_ist_timestamp
from .copper_market_brain_direction_audit import _session_quality
from .copper_research_brain import clean_ohlcv
from .current_mind_click_sampler import deterministic_clicks

CLICKS=20
SEED="COPPER_CURRENT_MIND_V1_20_CLICKS"

def audit_replay_integrity(candles, report):
    rows=clean_ohlcv(candles); quality=_session_quality(rows)
    complete_days={d for d,q in quality.items() if q.get("primary_score_eligible")}
    complete_rows=[r for r in rows if parse_ist_timestamp(r[0]).date() in complete_days]
    scheduled=deterministic_clicks(complete_rows,clicks_per_session=CLICKS,seed=SEED,warmup_bars=24,tail_bars=12,min_global_index=50)
    scheduled_ts=[parse_ist_timestamp(x["click_timestamp"]) for x in scheduled]
    decisions=report.get("decisions",[])
    evaluated_ts=[parse_ist_timestamp(x["click_timestamp"]) for x in decisions]
    missing=sorted(set(scheduled_ts)-set(evaluated_ts)); unexpected=sorted(set(evaluated_ts)-set(scheduled_ts))
    by_day=Counter(x.date().isoformat() for x in evaluated_ts)
    actions=Counter((x.get("decision") or {}).get("action") for x in decisions)
    trades=[x for x in decisions if (x.get("decision") or {}).get("action") in {"BUY_CE","BUY_PE"}]
    resolved=[x for x in trades if (x.get("outcome") or {}).get("result") in {"TARGET","STOP"}]
    realized=[float(x["outcome"]["realized_r"]) for x in resolved]
    outcomes=Counter((x.get("outcome") or {}).get("result") for x in trades)
    missed=sum(bool((x.get("outcome") or {}).get("future_move_without_setup")) for x in decisions if (x.get("decision") or {}).get("action") in {"WAIT","NO_TRADE"})
    headline={"actions":dict(actions),"trades":len(trades),"resolved_trades":len(resolved),"targets":outcomes.get("TARGET",0),"stops":outcomes.get("STOP",0),"no_entry":outcomes.get("NO_ENTRY",0),"session_end":outcomes.get("SESSION_END",0),"expectancy_r_resolved":round(mean(realized),3) if realized else None,"missed_large_moves_after_abstention":missed}
    reported={k:report.get(k) for k in headline}; reported["actions"]=report.get("actions")
    mismatches={k:{"recomputed":headline[k],"reported":reported.get(k)} for k in headline if headline[k]!=reported.get(k)}
    index_by_ts={parse_ist_timestamp(r[0]):i for i,r in enumerate(rows)}
    missing_detail=[{"timestamp":x.isoformat(),"global_index":index_by_ts.get(x),"reason":"GLOBAL_INDEX_LT_50" if index_by_ts.get(x) is not None and index_by_ts[x]<50 else "UNKNOWN"} for x in missing]
    same_bar=sum(bool((x.get("outcome") or {}).get("same_bar_ambiguous")) for x in trades)
    invalid=outcomes.get("INVALID_LEVELS",0)
    return {"mode":"CURRENT_MIND_REPLAY_INTEGRITY_AUDIT_V1","raw_candles":len(candles),"clean_candles":len(rows),"invalid_or_dropped_candles":len(candles)-len(rows),"complete_sessions":len(complete_days),"scheduled_clicks":len(scheduled_ts),"evaluated_clicks":len(evaluated_ts),"missing_scheduled_clicks":missing_detail,"unexpected_evaluated_clicks":[x.isoformat() for x in unexpected],"evaluated_clicks_by_session":dict(sorted(by_day.items())),"headline_recalculation":headline,"headline_mismatches":mismatches,"same_bar_target_stop_ambiguities":same_bar,"invalid_trade_levels":invalid,"checks":{"headline_arithmetic_exact":not mismatches,"click_schedule_exact":not missing and not unexpected,"all_complete_sessions_have_20_evaluated_clicks":all(by_day.get(d.isoformat(),0)==CLICKS for d in complete_days),"no_same_bar_target_stop_ambiguity":same_bar==0,"no_invalid_trade_levels":invalid==0},"limitations":["Internal consistency only; stored OHLCV still requires independent exchange/vendor certification.","Historical option premium/IV/Greeks/P&L remain unavailable and must not be inferred."]}
