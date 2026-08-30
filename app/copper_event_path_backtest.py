from __future__ import annotations

from collections import Counter
from statistics import mean, median

from .commodity_time import parse_ist_timestamp
from .copper_market_brain_direction_audit import (
    PRIMARY_END, PRIMARY_START, REFERENCE_CONTRACT, _session_quality,
)
from .copper_research_brain import (
    _build_copper_snapshot_clean, _precompute_information_quality,
    brain_a_signal, brain_b_signal, clean_ohlcv,
)

DEFAULT_SAMPLE_EVERY_BARS=3
TARGET_PCT=0.20
STOP_PCT=0.15
AMBIGUOUS_POLICY="CONSERVATIVE_STOP"


def _event_path(rows,index,signal,target_pct=TARGET_PCT,stop_pct=STOP_PCT):
    entry=float(rows[index][4])
    day=parse_ist_timestamp(rows[index][0]).date()
    if signal=="BUY":
        target=entry*(1+target_pct/100.0)
        stop=entry*(1-stop_pct/100.0)
        option_side="CE"
    elif signal=="SELL":
        target=entry*(1-target_pct/100.0)
        stop=entry*(1+stop_pct/100.0)
        option_side="PE"
    else:
        return None

    max_fav=max_adv=0.0
    for j in range(index+1,len(rows)):
        row=rows[j]
        stamp=parse_ist_timestamp(row[0])
        if stamp.date()!=day:
            break
        high=float(row[2]); low=float(row[3])
        elapsed=(stamp-parse_ist_timestamp(rows[index][0])).total_seconds()/60.0
        if signal=="BUY":
            max_fav=max(max_fav,(high/entry-1)*100)
            max_adv=max(max_adv,(entry/low-1)*100)
            hit_target=high>=target
            hit_stop=low<=stop
        else:
            max_fav=max(max_fav,(entry/low-1)*100)
            max_adv=max(max_adv,(high/entry-1)*100)
            hit_target=low<=target
            hit_stop=high>=stop
        if hit_target and hit_stop:
            return {"outcome":"STOP_FIRST_CONSERVATIVE","minutes_to_event":elapsed,
                    "option_side_intent":option_side,"entry_reference_price":entry,
                    "target_reference_price":target,"stop_reference_price":stop,
                    "mfe_pct":max_fav,"mae_pct":max_adv,"ambiguous_same_bar":True}
        if hit_target:
            return {"outcome":"TARGET_FIRST","minutes_to_event":elapsed,
                    "option_side_intent":option_side,"entry_reference_price":entry,
                    "target_reference_price":target,"stop_reference_price":stop,
                    "mfe_pct":max_fav,"mae_pct":max_adv,"ambiguous_same_bar":False}
        if hit_stop:
            return {"outcome":"STOP_FIRST","minutes_to_event":elapsed,
                    "option_side_intent":option_side,"entry_reference_price":entry,
                    "target_reference_price":target,"stop_reference_price":stop,
                    "mfe_pct":max_fav,"mae_pct":max_adv,"ambiguous_same_bar":False}

    return {"outcome":"SESSION_END_NO_EVENT","minutes_to_event":None,
            "option_side_intent":option_side,"entry_reference_price":entry,
            "target_reference_price":target,"stop_reference_price":stop,
            "mfe_pct":max_fav,"mae_pct":max_adv,"ambiguous_same_bar":False}


def _summary(obs):
    counts=Counter(x["outcome"] for x in obs)
    resolved=[x for x in obs if x["outcome"]!="SESSION_END_NO_EVENT"]
    targets=[x for x in obs if x["outcome"]=="TARGET_FIRST"]
    stops=[x for x in obs if x["outcome"] in {"STOP_FIRST","STOP_FIRST_CONSERVATIVE"}]
    times=[float(x["minutes_to_event"]) for x in resolved if x["minutes_to_event"] is not None]
    return {
        "setups":len(obs),
        "target_first":len(targets),
        "stop_first":len(stops),
        "session_end_no_event":counts["SESSION_END_NO_EVENT"],
        "target_first_pct_all_setups":round(len(targets)/len(obs)*100,2) if obs else 0.0,
        "target_first_pct_resolved":round(len(targets)/len(resolved)*100,2) if resolved else 0.0,
        "median_minutes_to_event":round(median(times),1) if times else None,
        "avg_minutes_to_event":round(mean(times),1) if times else None,
        "avg_mfe_pct":round(mean(x["mfe_pct"] for x in obs),4) if obs else 0.0,
        "avg_mae_pct":round(mean(x["mae_pct"] for x in obs),4) if obs else 0.0,
        "same_bar_ambiguous_count":sum(bool(x["ambiguous_same_bar"]) for x in obs),
    }


def evaluate_event_path(candles,sample_every_bars=DEFAULT_SAMPLE_EVERY_BARS,
                        target_pct=TARGET_PCT,stop_pct=STOP_PCT):
    rows=clean_ohlcv(candles)
    quality=_session_quality(rows)
    info=_precompute_information_quality(rows)
    by_brain={"A":[],"B":[]}
    step=max(1,int(sample_every_bars))
    for i in range(50,len(rows),step):
        stamp=parse_ist_timestamp(rows[i][0])
        if not (quality.get(stamp.date()) or {}).get("primary_score_eligible"):
            continue
        features=_build_copper_snapshot_clean(rows,i,information_quality=info)
        for brain,signal in {"A":brain_a_signal(features),"B":brain_b_signal(features)}.items():
            if signal=="NO_TRADE":
                continue
            path=_event_path(rows,i,signal,target_pct,stop_pct)
            if path:
                by_brain[brain].append({"timestamp":stamp.isoformat(),"signal":signal,**path})

    return {
        "mode":"COPPER_EVENT_PATH_BACKTEST_V1",
        "research_only":True,
        "trade_instrument":"OPTIONS",
        "underlying_reference_role":"REFERENCE_ONLY",
        "futures_pnl_calculated":False,
        "option_pnl_calculated":False,
        "target_pct":target_pct,"stop_pct":stop_pct,
        "target_stop_source":"FIXED_DIAGNOSTIC_REFERENCE_MOVE_NOT_PRODUCTION_RULE",
        "same_session_only":True,
        "ambiguous_same_5m_bar_policy":AMBIGUOUS_POLICY,
        "sample_every_bars":step,
        "sample_interval_minutes":step*5,
        "brains":{
            brain:{
                "overall":_summary(obs),
                "BUY_CE":_summary([x for x in obs if x["signal"]=="BUY"]),
                "SELL_PE":_summary([x for x in obs if x["signal"]=="SELL"]),
                "observations":obs,
            } for brain,obs in by_brain.items()
        },
        "guardrails":[
            "Every setup is frozen before future candles are inspected.",
            "Target/stop ordering never crosses a trading-date boundary.",
            "If target and stop both occur inside one 5-minute candle, stop is counted first because intrabar order is unknowable.",
            "Sparse provider sessions are excluded from the primary sample.",
            "Overlapping checkpoints are descriptive setup observations, not independent trades.",
            "Fixed 0.20%/0.15% reference moves diagnose path behavior only; they are not tuned or promoted strategy thresholds.",
            "CE/PE labels express directional intent only; no historical option premium is fabricated.",
        ],
    }


async def run_event_path_from_store(store,sample_every_bars=DEFAULT_SAMPLE_EVERY_BARS):
    await store.initialize()
    segments=await store.read_symbol_contract_segments("COPPER",5,PRIMARY_START,PRIMARY_END)
    target=next((s for s in segments if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT),None)
    if not target:
        raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    candles=target.get("candles") or []
    report=evaluate_event_path(candles,sample_every_bars)
    report["reference_contract"]={
        "trading_symbol":target.get("trading_symbol"),"expiry_date":target.get("expiry_date"),
        "candles":len(candles),"start":str(candles[0][0]) if candles else None,
        "end":str(candles[-1][0]) if candles else None,
    }
    return report
