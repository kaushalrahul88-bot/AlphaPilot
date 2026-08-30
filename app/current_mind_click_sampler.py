from __future__ import annotations
import hashlib,random
from datetime import timedelta
from .commodity_time import parse_ist_timestamp

def deterministic_clicks(candles, clicks_per_session=10, seed="CURRENT_MIND_V1", warmup_bars=24, tail_bars=12):
    """Sample user-like clicks without consulting future outcomes."""
    by_day={}
    for c in candles:
        ts=str(c.get("timestamp") or c.get("time") or c.get("datetime"))
        if not ts:continue
        d=parse_ist_timestamp(ts).date().isoformat()
        by_day.setdefault(d,[]).append(ts)
    out=[]
    for day,stamps in sorted(by_day.items()):
        stamps=sorted(stamps,key=parse_ist_timestamp)
        eligible=stamps[warmup_bars:len(stamps)-tail_bars if tail_bars else None]
        if not eligible:continue
        n=min(clicks_per_session,len(eligible))
        day_seed=int(hashlib.sha256(f"{seed}:{day}".encode()).hexdigest()[:16],16)
        rng=random.Random(day_seed)
        chosen=sorted(rng.sample(eligible,n),key=parse_ist_timestamp)
        out.extend({"session":day,"click_timestamp":x,"sampling":"DETERMINISTIC_RANDOM_WITHIN_SESSION"} for x in chosen)
    return out

def sampler_contract():
    return {"mode":"CURRENT_MIND_CLICK_SAMPLER_V1","default_clicks_per_session":10,
      "outcome_blind":True,"same_schedule_for_all_brains":True,
      "rules":["Clicks are selected from timestamps only; future price outcomes are never inspected.",
               "Warm-up bars provide enough current-session perception history.",
               "Tail bars are reserved so setup follow-through can be observed before session end.",
               "Weekends/holidays produce no clicks because no primary-market candles exist.",
               "The deterministic seed freezes the click set before results are scored."]}
