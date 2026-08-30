from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from .commodity_time import parse_ist_timestamp
from .copper_market_brain_direction_audit import PRIMARY_END, PRIMARY_START, REFERENCE_CONTRACT, _session_quality
from .copper_research_brain import clean_ohlcv
from .mcx_calendar import mcx_metal_day_schedule

def _expected_stamps(day):
    out=[]
    sched=mcx_metal_day_schedule(day)
    for w in sched["session_windows"]:
        sh,sm=map(int,w["start"].split(":")); eh,em=map(int,w["end"].split(":"))
        cur=datetime(day.year,day.month,day.day,sh,sm,tzinfo=parse_ist_timestamp(f"{day.isoformat()}T09:00:00+05:30").tzinfo)
        end=datetime(day.year,day.month,day.day,eh,em,tzinfo=cur.tzinfo)
        while cur<end:
            out.append(cur); cur+=timedelta(minutes=5)
    return out

def audit_copper_replay_data(candles, trading_symbol=None, expiry_date=None):
    raw=list(candles or [])
    clean=clean_ohlcv(raw)
    parsed=[]
    malformed_ts=0
    for r in clean:
        try: parsed.append(parse_ist_timestamp(r[0]))
        except Exception: malformed_ts+=1
    ts_counts=Counter(parsed)
    duplicate_ts=sorted(ts for ts,n in ts_counts.items() if n>1)
    off_grid=sorted(ts for ts in parsed if ts.minute%5 or ts.second or ts.microsecond)
    non_monotonic=sum(parsed[i]<=parsed[i-1] for i in range(1,len(parsed)))
    neg_volume=sum(float(r[5])<0 for r in clean)
    neg_oi=sum(r[6] is not None and float(r[6])<0 for r in clean)

    grouped=defaultdict(list)
    for r in clean:
        try: grouped[parse_ist_timestamp(r[0]).date()].append(r)
        except Exception: pass
    quality=_session_quality(clean)
    sessions=[]
    outside=[]
    missing_complete=[]
    for day in sorted(grouped):
        obs={parse_ist_timestamp(r[0]).replace(second=0,microsecond=0) for r in grouped[day]}
        expected=set(_expected_stamps(day))
        missing=sorted(expected-obs)
        extra=sorted(obs-expected)
        q=quality.get(day) or {}
        if q.get("primary_score_eligible"):
            missing_complete.extend(missing)
        outside.extend(extra)
        sessions.append({
            "date":day.isoformat(),
            "expected_5m_bars":len(expected),
            "observed_unique_5m_bars":len(obs),
            "coverage_pct":round(len(obs)/len(expected)*100,3) if expected else 0.0,
            "primary_score_eligible":bool(q.get("primary_score_eligible")),
            "missing_bar_count":len(missing),
            "outside_session_bar_count":len(extra),
            "first":min(obs).isoformat() if obs else None,
            "last":max(obs).isoformat() if obs else None,
            "missing_bars":[x.isoformat() for x in missing[:50]],
            "outside_session_bars":[x.isoformat() for x in extra[:50]],
        })

    complete=[x for x in sessions if x["primary_score_eligible"]]
    partial=[x for x in sessions if not x["primary_score_eligible"]]
    exact_complete=[x for x in complete if x["missing_bar_count"]==0 and x["outside_session_bar_count"]==0]
    return {
      "mode":"COPPER_CURRENT_MIND_DATA_INTEGRITY_AUDIT_V1",
      "reference_contract":trading_symbol or REFERENCE_CONTRACT,
      "expiry_date":expiry_date,
      "window":{"start":PRIMARY_START.isoformat(),"end":PRIMARY_END.isoformat()},
      "raw_candles":len(raw),"clean_candles":len(clean),"dropped_by_ohlcv_cleaning":len(raw)-len(clean),
      "malformed_timestamp_rows":malformed_ts,"duplicate_timestamp_count":len(duplicate_ts),
      "duplicate_timestamps":[x.isoformat() for x in duplicate_ts[:100]],
      "off_5m_grid_count":len(off_grid),"off_5m_grid":[x.isoformat() for x in off_grid[:100]],
      "non_monotonic_pairs":non_monotonic,"negative_volume_rows":neg_volume,"negative_oi_rows":neg_oi,
      "complete_sessions":len(complete),"exact_100pct_complete_sessions":len(exact_complete),
      "partial_or_ineligible_sessions":[x["date"] for x in partial],
      "missing_bars_inside_primary_complete_sessions":len(missing_complete),
      "outside_session_bars":len(outside),
      "sessions":sessions,
      "checks":{
        "contract_matches_reference":str(trading_symbol or REFERENCE_CONTRACT).upper()==REFERENCE_CONTRACT,
        "no_rows_dropped_by_ohlcv_cleaning":len(raw)==len(clean),
        "timestamps_parse":malformed_ts==0,
        "timestamps_unique":not duplicate_ts,
        "timestamps_on_5m_grid":not off_grid,
        "strictly_increasing":non_monotonic==0,
        "nonnegative_volume":neg_volume==0,
        "nonnegative_open_interest":neg_oi==0,
        "no_outside_session_bars":not outside,
        "all_primary_sessions_exactly_complete":len(missing_complete)==0,
        "partial_aug27_aug28_only":set(x["date"] for x in partial)=={"2026-08-27","2026-08-28"},
      },
      "certification_scope":{
        "internal_store_integrity":"AUDITED",
        "external_exchange_or_vendor_equality":"NOT_YET_CERTIFIED",
        "note":"This validates the exact stored replay input structurally. Exact candle-by-candle equality to an independent MCX/Groww historical source requires a separately acquired authoritative export/API response."
      }
    }

async def run_copper_replay_data_audit_from_store(store):
    await store.initialize()
    segs=await store.read_symbol_contract_segments("COPPER",5,PRIMARY_START,PRIMARY_END)
    target=next((s for s in segs if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT),None)
    if not target: raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    return audit_copper_replay_data(target.get("candles") or [],target.get("trading_symbol"),target.get("expiry_date"))
