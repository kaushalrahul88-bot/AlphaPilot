from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .copper_market_brain_direction_audit import (
    PRIMARY_END, PRIMARY_START, REFERENCE_CONTRACT, _session_quality,
)
from .copper_research_brain import (
    _build_copper_snapshot_clean, _precompute_information_quality, clean_ohlcv,
)
from .copper_experience_memory import (
    FEATURES, build_experiences, query_memory, _snapshot_vector,
)
from .copper_point_in_time_context import visible_at
from .china_copper_macro_context import china_copper_macro_records
from .copper_historical_news import fetch_copper_historical_news
from .copper_historical_news_integrity_audit import audit_historical_news_records
from .copper_news_intelligence import apply_news_intelligence
from .current_mind_click_sampler import deterministic_clicks
from .current_mind_integrated_replay import current_mind_click
from .current_mind_replay_scorecard import replay_scorecard

CLICKS_PER_COMPLETE_SESSION=20
MEMORY_K=75
MEMORY_MIN_RESOLVED_EACH_SIDE=15
MEMORY_MIN_EDGE_PP=10.0
MISSED_MOVE_PCT=0.40
TARGET_R=1.5
RECENT_TRIGGER_BARS=3
RECENT_INVALIDATION_BARS=6


def _f(value, default=None):
    try:return float(value)
    except (TypeError,ValueError):return default


def _safe_memory_pool(experiences, click):
    """Only include experiences whose outcomes were genuinely resolved before click."""
    safe=[]
    for e in experiences:
        ts=parse_ist_timestamp(e["timestamp"])
        if ts>=click:continue
        minutes=e.get("minutes_to_event")
        if minutes is None:
            if ts.date()<click.date():safe.append(e)
            continue
        if ts+timedelta(minutes=float(minutes))<=click:safe.append(e)
    return safe


def _memory_evidence(experiences, features, click):
    q={"timestamp":click.isoformat(),"vector":_snapshot_vector(features),
       "structure":features.get("structure"),"opening_range_break":features.get("opening_range_break"),
       "price_oi_state":features.get("price_oi_state")}
    result=query_memory(_safe_memory_pool(experiences,click),q,MEMORY_K)
    item={"lane":"EXPERIENCE","stance":"UNKNOWN","source":"walk_forward_memory","detail":result}
    if result.get("status")!="READY":return item
    b=(result.get("by_direction") or {}).get("BULLISH") or {}
    s=(result.get("by_direction") or {}).get("BEARISH") or {}
    if min(int(b.get("resolved") or 0),int(s.get("resolved") or 0))<MEMORY_MIN_RESOLVED_EACH_SIDE:return item
    bp=_f(b.get("target_first_pct_resolved"));sp=_f(s.get("target_first_pct_resolved"))
    if bp is None or sp is None or abs(bp-sp)<MEMORY_MIN_EDGE_PP:return item
    item["stance"]="BULLISH" if bp>sp else "BEARISH"
    item["edge_pp"]=round(abs(bp-sp),2)
    return item


def _macro_evidence(click):
    records=visible_at(china_copper_macro_records(),click.isoformat())
    if not records:return {"lane":"MACRO","stance":"UNKNOWN","source":"China NBS","detail":[]}
    bearish=0;bullish=0
    for r in records:
        v=r.get("value") or {}; event=v.get("event")
        if event=="CHINA_MANUFACTURING_PMI":
            p=_f(v.get("actual"))
            if p is not None: bearish+=p<50; bullish+=p>=50
        elif event=="CHINA_FIXED_ASSET_INVESTMENT":
            p=_f(v.get("yoy_pct"))
            if p is not None: bearish+=p<0; bullish+=p>0
        elif event=="CHINA_INDUSTRIAL_VALUE_ADDED":
            p=_f(v.get("yoy_pct"))
            if p is not None: bullish+=p>0; bearish+=p<0
    stance="BULLISH" if bullish>bearish else "BEARISH" if bearish>bullish else "UNKNOWN"
    return {"lane":"MACRO","stance":stance,"source":"China NBS","detail":[r.get("value") for r in records]}


def _news_evidence(click, records):
    cutoff=click-timedelta(hours=8)
    visible=[r for r in (records or []) if cutoff<=parse_ist_timestamp(r["available_at"])<=click]
    bullish=sum((r.get("value") or {}).get("sentiment")=="BULLISH" for r in visible)
    bearish=sum((r.get("value") or {}).get("sentiment")=="BEARISH" for r in visible)
    stance="BULLISH" if bullish>bearish else "BEARISH" if bearish>bullish else "UNKNOWN"
    return {"lane":"NEWS","stance":stance,"source":"GDELT_timestamped_headlines","detail":{"visible_8h":len(visible),"bullish":bullish,"bearish":bearish,"latest":[r.get("value") for r in visible[-5:]]}}


def _evidence_items(features, memory_item, macro_item, news_item=None):
    structure=str(features.get("structure") or "UNKNOWN")
    ret15=_f(features.get("return_15m_pct"),0.0)
    rel=_f(features.get("time_adjusted_relative_volume"))
    if rel is None:rel=_f(features.get("relative_volume"))
    items=[
      {"lane":"STRUCTURE","stance":"BULLISH" if structure=="UPTREND" else "BEARISH" if structure=="DOWNTREND" else "UNKNOWN",
       "source":"market_structure","detail":{"structure":structure}},
      {"lane":"STRUCTURE","stance":"BULLISH" if ret15>0 else "BEARISH" if ret15<0 else "UNKNOWN",
       "source":"short_term_momentum","detail":{"return_15m_pct":ret15}},
      {"lane":"PARTICIPATION","stance":("BULLISH" if ret15>0 else "BEARISH") if rel is not None and rel>=1.0 and ret15!=0 else "UNKNOWN",
       "source":"time_adjusted_volume","detail":{"relative_volume":rel}},
      memory_item,macro_item,
    ]
    if news_item is not None:items.append(news_item)
    return items


def _regime_features(features):
    atr=_f(features.get("atr_pct"))
    pos=_f(features.get("session_range_position"))
    vwap=_f(features.get("session_vwap_gap_pct"),0.0)
    rel=_f(features.get("time_adjusted_relative_volume"))
    if rel is None:rel=_f(features.get("relative_volume"))
    if atr is None:vol="UNKNOWN"
    elif atr>=0.35:vol="HIGH"
    elif atr<=0.12:vol="LOW"
    else:vol="NORMAL"
    if pos is not None and pos>=0.88 and vwap>=0.15:location="EXTENDED_ABOVE_VALUE"
    elif pos is not None and pos<=0.12 and vwap<=-0.15:location="EXTENDED_BELOW_VALUE"
    else:location="IN_VALUE"
    br=str(features.get("opening_range_break") or "")
    opening="BREAKOUT" if br=="ABOVE" else "BREAKDOWN" if br=="BELOW" else "BALANCED"
    return {"trend_structure":features.get("structure") or "UNKNOWN","volatility_regime":vol,
            "location":location,"participation":"WEAKENING" if rel is not None and rel<0.8 else "NORMAL",
            "opening_behavior":opening}


def _dominant_direction(items):
    lanes=defaultdict(set)
    for x in items:
        stance=str(x.get("stance") or "UNKNOWN")
        if stance in {"BULLISH","BEARISH"}:lanes[x.get("lane","OTHER")].add(stance)
    bull=sum(v=={"BULLISH"} for v in lanes.values())
    bear=sum(v=={"BEARISH"} for v in lanes.values())
    if max(bull,bear)<2 or bull==bear:return None
    return "BULLISH" if bull>bear else "BEARISH"


def _trade_geometry(rows,index,direction,features):
    recent3=rows[max(0,index-RECENT_TRIGGER_BARS+1):index+1]
    recent6=rows[max(0,index-RECENT_INVALIDATION_BARS+1):index+1]
    if direction=="BULLISH":
        entry=max(float(r[2]) for r in recent3)
        stop=min(float(r[3]) for r in recent6)
        risk=entry-stop
        target=entry+TARGET_R*risk
        trigger=f"Break and accept above {entry:.2f}"
        invalidation=f"Trade invalid below {stop:.2f}"
    else:
        entry=min(float(r[3]) for r in recent3)
        stop=max(float(r[2]) for r in recent6)
        risk=stop-entry
        target=entry-TARGET_R*risk
        trigger=f"Break and accept below {entry:.2f}"
        invalidation=f"Trade invalid above {stop:.2f}"
    if risk<=0:return {}
    return {"confirmation":"At least two independent evidence lanes align with the regime.",
            "entry_trigger":trigger,"invalidation":invalidation,
            "target_logic":f"Structural {TARGET_R:.1f}R target at {target:.2f}",
            "risk_reward_basis":f"Frozen structural target {TARGET_R:.1f}R",
            "entry_price":entry,"stop_price":stop,"target_price":target}


def _resolve_setup(rows,index,decision):
    if decision.get("action") not in {"BUY_CE","BUY_PE"}:return None
    risk=(decision.get("risk_review") or {}).get("risk_points")
    thesis=decision.get("thesis") or {}
    # Numeric levels live in risk review inputs only indirectly; reconstruct from thesis text is prohibited.
    # Replay caller stores frozen levels separately in decision['replay_levels'].
    lv=decision.get("replay_levels") or {}
    entry=_f(lv.get("entry"));stop=_f(lv.get("stop"));target=_f(lv.get("target"))
    if None in {entry,stop,target} or not risk:return {"result":"INVALID_LEVELS"}
    bullish=decision["action"]=="BUY_CE"; entered=False; entry_time=None
    mfe=mae=0.0
    for row in rows[index+1:]:
        ts=parse_ist_timestamp(row[0])
        if ts.date()!=parse_ist_timestamp(rows[index][0]).date():break
        high=float(row[2]);low=float(row[3])
        if not entered:
            if (bullish and high>=entry) or ((not bullish) and low<=entry):
                entered=True;entry_time=ts
            else:continue
        if bullish:
            mfe=max(mfe,(high-entry)/risk);mae=max(mae,(entry-low)/risk)
            hit_t=high>=target;hit_s=low<=stop
        else:
            mfe=max(mfe,(entry-low)/risk);mae=max(mae,(high-entry)/risk)
            hit_t=low<=target;hit_s=high>=stop
        if hit_t and hit_s:return {"result":"STOP","realized_r":-1.0,"mfe_r":round(mfe,3),"mae_r":round(mae,3),"entry_at":entry_time.isoformat(),"exit_at":ts.isoformat(),"same_bar_ambiguous":True}
        if hit_s:return {"result":"STOP","realized_r":-1.0,"mfe_r":round(mfe,3),"mae_r":round(mae,3),"entry_at":entry_time.isoformat(),"exit_at":ts.isoformat()}
        if hit_t:return {"result":"TARGET","realized_r":TARGET_R,"mfe_r":round(mfe,3),"mae_r":round(mae,3),"entry_at":entry_time.isoformat(),"exit_at":ts.isoformat()}
    return {"result":"NO_ENTRY" if not entered else "SESSION_END","realized_r":0.0,"mfe_r":round(mfe,3),"mae_r":round(mae,3),"entry_at":entry_time.isoformat() if entry_time else None}


def _missed_move(rows,index):
    day=parse_ist_timestamp(rows[index][0]).date();entry=float(rows[index][4]);high=entry;low=entry
    for row in rows[index+1:]:
        if parse_ist_timestamp(row[0]).date()!=day:break
        high=max(high,float(row[2]));low=min(low,float(row[3]))
    up=(high/entry-1)*100;down=(entry/low-1)*100
    return {"future_move_without_setup":max(up,down)>=MISSED_MOVE_PCT,
            "max_up_pct":round(up,4),"max_down_pct":round(down,4),
            "large_move_threshold_pct":MISSED_MOVE_PCT}


def evaluate_current_mind_replay(candles, news_records=None, news_metadata=None):
    rows=clean_ohlcv(candles);quality=_session_quality(rows);info=_precompute_information_quality(rows)
    complete_days={d for d,q in quality.items() if q.get("primary_score_eligible")}
    complete_rows=[r for r in rows if parse_ist_timestamp(r[0]).date() in complete_days]
    clicks=deterministic_clicks(complete_rows,clicks_per_session=CLICKS_PER_COMPLETE_SESSION,
                                seed="COPPER_CURRENT_MIND_V1_20_CLICKS",warmup_bars=24,tail_bars=12,
                                min_global_index=50)
    click_set={parse_ist_timestamp(x["click_timestamp"]):x for x in clicks}
    index_by_ts={parse_ist_timestamp(r[0]):i for i,r in enumerate(rows)}
    experiences=build_experiences(rows,3)
    decisions=[];memory_cases=[]
    macro_records=china_copper_macro_records()
    for click in sorted(click_set):
        index=index_by_ts.get(click)
        if index is None:
            raise RuntimeError(f"Scheduled click timestamp missing from replay rows: {click.isoformat()}")
        # The sampler's per-session 24-bar warm-up is the frozen eligibility rule.
        # Do not silently impose a second global-history gate here: it removed
        # three valid first-session clicks and broke the 20-click/session contract.
        features=_build_copper_snapshot_clean(rows,index,information_quality=info)
        mem=_memory_evidence(experiences,features,click);macro=_macro_evidence(click)
        news=_news_evidence(click,news_records) if news_records is not None else None
        evidence=_evidence_items(features,mem,macro,news)
        direction=_dominant_direction(evidence)
        market=_regime_features(features)
        geom=_trade_geometry(rows,index,direction,features) if direction else {}
        market.update(geom)
        context=[{"series":"MCX_COPPER","observed_at":click.isoformat(),"available_at":click.isoformat(),
                  "source":REFERENCE_CONTRACT,"value":{"price":features.get("price")},"quality":"OBSERVED"}]
        context.extend([r for r in macro_records if parse_ist_timestamp(r["available_at"])<=click])
        if news_records is not None:
            visible_news=[r for r in news_records if parse_ist_timestamp(r["available_at"])<=click]
            if visible_news:context.append(visible_news[-1])
        journal=current_mind_click(click_timestamp=click.isoformat(),context_records=context,
          market_features=market,evidence_items=evidence,memory_cases=memory_cases)
        decision=journal["decision"]
        if decision.get("action") in {"BUY_CE","BUY_PE"} and geom:
            decision["replay_levels"]={"entry":geom["entry_price"],"stop":geom["stop_price"],"target":geom["target_price"]}
        outcome=_resolve_setup(rows,index,decision)
        if outcome is None:outcome=_missed_move(rows,index)
        journal["outcome"]=outcome
        decisions.append(journal)
        # Add only completed historical decision cases for later clicks; outcome is now known after replay reveal.
        memory_cases.append({"regime":journal.get("regime"),"evidence":journal.get("evidence"),
                             "action":decision.get("action"),"outcome":outcome,
                             "decision_fingerprint":journal.get("decision_fingerprint")})
    score=replay_scorecard([dict(x["decision"],outcome=x.get("outcome"),
                                 lookahead_violation=False,
                                 contradictions=x["decision"].get("contradictions",[]),
                                 missing_context=x["decision"].get("missing_context",[]))
                              for x in decisions])
    trades=[x for x in decisions if x["decision"].get("action") in {"BUY_CE","BUY_PE"}]
    resolved=[x for x in trades if (x.get("outcome") or {}).get("result") in {"TARGET","STOP"}]
    rs=[float(x["outcome"]["realized_r"]) for x in resolved]
    return {"mode":"COPPER_CURRENT_MIND_20_CLICK_REPLAY_V1","research_only":True,
      "current_mind_frozen":True,"clicks_per_complete_session":CLICKS_PER_COMPLETE_SESSION,
      "reference_contract":REFERENCE_CONTRACT,"candles":len(rows),
      "complete_sessions":len(complete_days),"complete_session_dates":sorted(d.isoformat() for d in complete_days),
      "excluded_partial_sessions":sorted(q["date"] for q in quality.values() if not q.get("primary_score_eligible")),
      "scheduled_clicks":len(clicks),"evaluated_clicks":len(decisions),
      "click_coverage_exact":len(clicks)==len(decisions)==CLICKS_PER_COMPLETE_SESSION*len(complete_days),
      "actions":dict(Counter(x["decision"].get("action") for x in decisions)),
      "trades":len(trades),"resolved_trades":len(resolved),
      "targets":sum((x.get("outcome") or {}).get("result")=="TARGET" for x in trades),
      "stops":sum((x.get("outcome") or {}).get("result")=="STOP" for x in trades),
      "no_entry":sum((x.get("outcome") or {}).get("result")=="NO_ENTRY" for x in trades),
      "session_end":sum((x.get("outcome") or {}).get("result")=="SESSION_END" for x in trades),
      "expectancy_r_resolved":round(mean(rs),3) if rs else None,
      "missed_large_moves_after_abstention":sum(bool((x.get("outcome") or {}).get("future_move_without_setup")) for x in decisions if x["decision"].get("action") in {"WAIT","NO_TRADE"}),
      "scorecard":score,"decisions":decisions,
      "data_context":{"mcx_5m":True,"china_macro_point_in_time":True,"experience_walk_forward":True,
                      "comex_intraday":False,"lme_intraday":False,"historical_news":news_records is not None,"historical_option_premium":False},
      "news_metadata":news_metadata if news_records is not None else None,
      "guardrails":["20 deterministic random clicks per complete trading session.","Provider-confirmed partial sessions excluded from primary replay.",
                    "Every feature uses data no later than the click.","Memory outcomes must resolve before the click before becoming eligible evidence.",
                    "Future bars are revealed only after the decision is frozen.","No historical option premium, IV, Greeks or option P&L is fabricated."]}


async def run_current_mind_replay_from_store(store):
    await store.initialize()
    segs=await store.read_symbol_contract_segments("COPPER",5,PRIMARY_START,PRIMARY_END)
    target=next((s for s in segs if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT),None)
    if not target:raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    report=evaluate_current_mind_replay(target.get("candles") or [])
    report["contract_metadata"]={"trading_symbol":target.get("trading_symbol"),"expiry_date":target.get("expiry_date")}
    return report


async def run_current_mind_news_replay_from_store(store, prevalidated_news_records=None, prevalidated_news_metadata=None):
    await store.initialize()
    segs=await store.read_symbol_contract_segments("COPPER",5,PRIMARY_START,PRIMARY_END)
    target=next((s for s in segs if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT),None)
    if not target:raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    if prevalidated_news_records is not None:
        directional=list(prevalidated_news_records)
        if not directional:raise RuntimeError("Prevalidated News Intelligence directional set is empty")
        meta=dict(prevalidated_news_metadata or {})
        meta["acquisition_mode"]="FROZEN_PREVALIDATED_NEWS_INTELLIGENCE"
        meta["network_refetch"]=False
    else:
        news=await fetch_copper_historical_news(PRIMARY_START,PRIMARY_END)
        if not news["records"]:raise RuntimeError("Historical Copper news fetch returned no timestamped records")
        integrity=audit_historical_news_records(news["records"])
        accepted=integrity.get("accepted_records") or []
        if not accepted:raise RuntimeError("Historical Copper news integrity audit accepted zero records")
        intelligence=apply_news_intelligence(accepted)
        directional=intelligence.get("allowed_records") or []
        if not directional:raise RuntimeError("News Intelligence allowed zero directional records; replay remains blocked")
        meta={
          **{k:v for k,v in news.items() if k!="records"},
          "integrity_audit_mode":integrity.get("mode"),"accepted_record_count":len(accepted),
          "accepted_dataset_sha256":integrity.get("accepted_dataset_sha256"),
          "classification_counts":integrity.get("classification_counts"),
          "news_intelligence_mode":intelligence.get("mode"),
          "news_intelligence_counts":intelligence.get("counts"),
          "news_intelligence_policy":intelligence.get("policy"),
        }
    report=evaluate_current_mind_replay(target.get("candles") or [],news_records=directional,news_metadata=meta)
    report["mode"]="COPPER_CURRENT_MIND_20_CLICK_REPLAY_WITH_HISTORICAL_NEWS_V1"
    report["comparison_variant"]="FROZEN_CURRENT_MIND_PLUS_TIMESTAMPED_NEWS"
    report["contract_metadata"]={"trading_symbol":target.get("trading_symbol"),"expiry_date":target.get("expiry_date")}
    return report
