from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from .backtest import IST, _historical, run_backtest
from .market_brain_context_research import SYMBOLS, _build

SETUP_SYMBOLS = ["RELIANCE","HDFCBANK","ICICIBANK","SBIN","TCS","INFY","TATASTEEL","MARUTI"]
FEATURES = ("breadth","flow","niftyPhase","bankPhase","leaders","financials","it","metals")
DYNAMIC_FEATURES = (
    "breadthImpulse",
    "flowImpulse",
    "leaderImpulse",
    "indexAlignment",
    "breadthPersistence",
    "flowPersistence",
)
MIN_GROUP_OBS = 12
DELTA_R_GATE = 0.20
DELTA_WIN_GATE = 8.0
V5_MIN_GROUP_OBS = 10
V5_DELTA_R_GATE = 0.20
V5_DELTA_WIN_GATE = 8.0
CONTEXT_WINDOW_START = "09:45"
CONTEXT_WINDOW_END = "14:30"

BREADTH_ORDER = {"BROAD_RISK_OFF":-1,"MIXED":0,"BROAD_RISK_ON":1}
FLOW_ORDER = {"SELLING_PRESSURE":-1,"BALANCED":0,"BUYING_PRESSURE":1}
LEADER_ORDER = {"0-2_LEADERS":0,"3-5_LEADERS":1,"6+_LEADERS":2}
PHASE_SIGN = {"ALIGNED_UP":1,"RECOVERY":1,"ALIGNED_DOWN":-1,"FADE":-1,"MIXED":0}


def _summary(sample):
    n = len(sample)
    if not n:
        return {"trades":0,"avg_r":0.0,"win_rate":0.0,"total_r":0.0}
    total = sum(float(x.get("r_multiple",0.0)) for x in sample)
    wins = sum(float(x.get("r_multiple",0.0)) > 0 for x in sample)
    return {"trades":n,"avg_r":round(total/n,3),"win_rate":round(wins/n*100.0,1),"total_r":round(total,2)}


def _effect_state(n, delta_r, delta_win, min_obs=MIN_GROUP_OBS):
    if n < min_obs:
        return "LOW_SAMPLE"
    if delta_r >= DELTA_R_GATE and delta_win >= DELTA_WIN_GATE:
        return "BOOST"
    if delta_r <= -DELTA_R_GATE and delta_win <= -DELTA_WIN_GATE:
        return "DRAG"
    return "MIXED"


def _minute_key(value):
    """Normalize Groww candle and scanner timestamps to the same IST minute key."""
    try:
        raw = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def _inside_context_window(value):
    key = _minute_key(value)
    if not key:
        return False
    hhmm = key[-5:]
    return CONTEXT_WINDOW_START <= hhmm <= CONTEXT_WINDOW_END


def _ordered_impulse(current, previous, order, positive, negative):
    current_rank = order.get(str(current))
    previous_rank = order.get(str(previous))
    if current_rank is None or previous_rank is None:
        return "UNKNOWN"
    if current_rank > previous_rank:
        return positive
    if current_rank < previous_rank:
        return negative
    return "STABLE"


def _dynamic_context(current, previous):
    nifty_sign = PHASE_SIGN.get(str(current.get("niftyPhase")))
    bank_sign = PHASE_SIGN.get(str(current.get("bankPhase")))
    if nifty_sign is None or bank_sign is None:
        alignment = "UNKNOWN"
    elif nifty_sign == bank_sign == 1:
        alignment = "BULLISH_ALIGNED"
    elif nifty_sign == bank_sign == -1:
        alignment = "BEARISH_ALIGNED"
    else:
        alignment = "DIVERGENT_OR_MIXED"

    current_breadth = str(current.get("breadth"))
    previous_breadth = str(previous.get("breadth"))
    if current_breadth == previous_breadth == "BROAD_RISK_ON":
        breadth_persistence = "PERSISTENT_RISK_ON"
    elif current_breadth == previous_breadth == "BROAD_RISK_OFF":
        breadth_persistence = "PERSISTENT_RISK_OFF"
    elif current_breadth not in BREADTH_ORDER or previous_breadth not in BREADTH_ORDER:
        breadth_persistence = "UNKNOWN"
    else:
        breadth_persistence = "MIXED_OR_CHANGING"

    current_flow = str(current.get("flow"))
    previous_flow = str(previous.get("flow"))
    if current_flow == previous_flow == "BUYING_PRESSURE":
        flow_persistence = "PERSISTENT_BUYING"
    elif current_flow == previous_flow == "SELLING_PRESSURE":
        flow_persistence = "PERSISTENT_SELLING"
    elif current_flow not in FLOW_ORDER or previous_flow not in FLOW_ORDER:
        flow_persistence = "UNKNOWN"
    else:
        flow_persistence = "BALANCED_OR_CHANGING"

    return {
        "breadthImpulse":_ordered_impulse(current_breadth,previous_breadth,BREADTH_ORDER,"IMPROVING","DETERIORATING"),
        "flowImpulse":_ordered_impulse(current_flow,previous_flow,FLOW_ORDER,"IMPROVING","DETERIORATING"),
        "leaderImpulse":_ordered_impulse(current.get("leaders"),previous.get("leaders"),LEADER_ORDER,"BROADENING","NARROWING"),
        "indexAlignment":alignment,
        "breadthPersistence":breadth_persistence,
        "flowPersistence":flow_persistence,
    }


def _dynamic_context_by_ts(context_obs):
    """Build setup-time dynamic features without cross-day carry or future leakage."""
    normalized = []
    for ctx in context_obs:
        key = _minute_key(ctx.get("ts"))
        if key:
            normalized.append((datetime.strptime(key, "%Y-%m-%d %H:%M"), key, ctx))
    normalized.sort(key=lambda item:item[0])

    out = {}
    previous = None
    for dt, key, ctx in normalized:
        if previous is not None:
            previous_dt, _, previous_ctx = previous
            if dt.date() == previous_dt.date() and dt - previous_dt == timedelta(minutes=15):
                out[key] = _dynamic_context(ctx, previous_ctx)
        previous = (dt, key, ctx)
    return out


def _build_dynamic_effects(matched):
    baseline = {
        direction:_summary([trade for trade in matched if trade.get("direction") == direction])
        for direction in ("LONG","SHORT")
    }
    groups = defaultdict(list)
    for trade in matched:
        direction = str(trade.get("direction","UNKNOWN"))
        ctx = trade.get("dynamic_context") or {}
        for feature in DYNAMIC_FEATURES:
            value = str(ctx.get(feature,"UNKNOWN"))
            if value != "UNKNOWN":
                groups[(direction,feature,value)].append(trade)

    rows = []
    for (direction,feature,value), sample in groups.items():
        summary = _summary(sample)
        direction_baseline = baseline.get(direction) or {"avg_r":0.0,"win_rate":0.0,"trades":0}
        delta_r = round(summary["avg_r"]-float(direction_baseline.get("avg_r",0.0)),3)
        delta_win = round(summary["win_rate"]-float(direction_baseline.get("win_rate",0.0)),1)
        rows.append({
            "label":f"{direction} · {feature}={value}",
            "direction":direction,"feature":feature,"value":value,
            **summary,
            "baseline_trades":direction_baseline.get("trades",0),
            "baseline_avg_r":direction_baseline.get("avg_r",0.0),
            "baseline_win_rate":direction_baseline.get("win_rate",0.0),
            "delta_avg_r":delta_r,"delta_win_rate_pp":delta_win,
            "state":_effect_state(summary["trades"],delta_r,delta_win),
        })
    rows.sort(key=lambda row:(abs(row["delta_avg_r"]),abs(row["delta_win_rate_pp"]),row["trades"]), reverse=True)
    eligible = [row for row in rows if row["trades"] >= MIN_GROUP_OBS]
    return {
        "baseline_by_direction":baseline,
        "effects":rows,
        "hypotheses_tested":len(rows),
        "eligible_hypotheses":len(eligible),
        "boosts":sum(row["state"] == "BOOST" for row in rows),
        "drags":sum(row["state"] == "DRAG" for row in rows),
        "fixed_effect_rules":{"min_group_trades":MIN_GROUP_OBS,"delta_avg_r":DELTA_R_GATE,"delta_win_rate_pp":DELTA_WIN_GATE},
        "feature_rules":{
            "breadthImpulse":"Ordinal change from the immediately prior same-day 15-minute breadth state",
            "flowImpulse":"Ordinal change from the immediately prior same-day 15-minute flow state",
            "leaderImpulse":"Ordinal change from the immediately prior same-day 15-minute leadership band",
            "indexAlignment":"Current NIFTY and BANKNIFTY phase signs agree bullish, agree bearish, or diverge/mix",
            "breadthPersistence":"Current and prior breadth both risk-on, both risk-off, or mixed/changing",
            "flowPersistence":"Current and prior flow both buying, both selling, or balanced/changing",
        },
    }


def _setup_archetypes(trade):
    """Frozen v5 archetypes using setup-time fields already emitted by the scanner backtest."""
    alpha = float(trade.get("mtf_alpha") or 0.0)
    rr = float(trade.get("underlying_rr") or 0.0)
    key = _minute_key(trade.get("timestamp"))
    hhmm = key[-5:] if key else "00:00"
    return {
        "alphaBand": "HIGH_ALPHA" if alpha >= 75.0 else "STANDARD_ALPHA",
        "rrBand": "HIGH_RR" if rr >= 2.0 else "STANDARD_RR",
        "timeBand": "EARLY_SETUP" if hhmm <= "11:30" else "LATE_SETUP",
    }


def _build_archetype_effects(matched):
    archetype_baselines = defaultdict(list)
    groups = defaultdict(list)
    archetype_counts = defaultdict(int)
    for trade in matched:
        direction = str(trade.get("direction","UNKNOWN"))
        ctx = trade.get("context") or {}
        archetypes = _setup_archetypes(trade)
        for axis, state in archetypes.items():
            archetype_baselines[(direction,axis,state)].append(trade)
            archetype_counts[f"{direction} · {axis}={state}"] += 1
            for feature in FEATURES:
                value = str(ctx.get(feature,"UNKNOWN"))
                if value == "UNKNOWN":
                    continue
                groups[(direction,axis,state,feature,value)].append(trade)

    baselines = {key:_summary(sample) for key,sample in archetype_baselines.items()}
    rows = []
    for (direction,axis,state,feature,value), sample in groups.items():
        s = _summary(sample)
        b = baselines.get((direction,axis,state)) or {"avg_r":0.0,"win_rate":0.0,"trades":0}
        delta_r = round(s["avg_r"]-float(b.get("avg_r",0.0)),3)
        delta_win = round(s["win_rate"]-float(b.get("win_rate",0.0)),1)
        rows.append({
            "label":f"{direction} · {axis}={state} × {feature}={value}",
            "direction":direction,"archetype_axis":axis,"archetype":state,"feature":feature,"value":value,
            **s,"baseline_trades":b.get("trades",0),"baseline_avg_r":b.get("avg_r",0.0),"baseline_win_rate":b.get("win_rate",0.0),
            "delta_avg_r":delta_r,"delta_win_rate_pp":delta_win,
            "state":_effect_state(s["trades"],delta_r,delta_win,V5_MIN_GROUP_OBS),
        })
    rows.sort(key=lambda x:(abs(x["delta_avg_r"]),abs(x["delta_win_rate_pp"]),x["trades"]), reverse=True)
    eligible = [x for x in rows if x["trades"] >= V5_MIN_GROUP_OBS]
    return {
        "archetype_counts":dict(archetype_counts),
        "effects":rows,
        "hypotheses_tested":len(rows),
        "eligible_hypotheses":len(eligible),
        "boosts":sum(x["state"]=="BOOST" for x in rows),
        "drags":sum(x["state"]=="DRAG" for x in rows),
        "fixed_effect_rules":{"min_group_trades":V5_MIN_GROUP_OBS,"delta_avg_r":V5_DELTA_R_GATE,"delta_win_rate_pp":V5_DELTA_WIN_GATE},
        "archetype_rules":{
            "alphaBand":"HIGH_ALPHA if MTF Alpha >=75, else STANDARD_ALPHA",
            "rrBand":"HIGH_RR if scanner underlying R:R >=2.0, else STANDARD_RR",
            "timeBand":"EARLY_SETUP through 11:30 IST, otherwise LATE_SETUP",
        },
    }


async def run_market_brain_setup_expectancy(provider, start_date: str, end_date: str):
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end-start).days > 16:
        raise ValueError("Market Brain v4/v5/v6 blocks are limited to 16 calendar days")

    context_start = start - timedelta(days=5)
    context_data, context_errors = {}, []
    async def fetch_one(symbol):
        try:
            rows = await _historical(provider, symbol, "15m", context_start, end)
            return symbol, rows, None
        except Exception as exc:
            return symbol, [], f"{exc.__class__.__name__}: {exc}"

    for i in range(0, len(SYMBOLS), 4):
        batch = await asyncio.gather(*(fetch_one(s) for s in SYMBOLS[i:i+4]))
        for symbol, rows, error in batch:
            context_data[symbol] = rows
            if error:
                context_errors.append({"symbol":symbol,"error":error})
        await asyncio.sleep(.15)

    raw_context_obs = _build(context_data)
    context_obs = []
    context_by_ts = {}
    for ctx in raw_context_obs:
        key = _minute_key(ctx.get("ts"))
        if not key:
            continue
        dt = datetime.strptime(key, "%Y-%m-%d %H:%M")
        if start <= dt <= end:
            context_obs.append(ctx)
            context_by_ts[key] = ctx
    dynamic_context_by_ts = _dynamic_context_by_ts(context_obs)

    backtest = await run_backtest(provider, SETUP_SYMBOLS, start_date, end_date, 1.5, None)
    trades = backtest.get("trades", [])
    eligible_trades = [t for t in trades if _inside_context_window(t.get("timestamp"))]
    outside_window_trades = [t for t in trades if not _inside_context_window(t.get("timestamp"))]
    matched = []
    dynamic_matched = []
    unmatched = []
    for trade in eligible_trades:
        key = _minute_key(trade.get("timestamp"))
        ctx = context_by_ts.get(key) if key else None
        if not ctx:
            unmatched.append({"symbol":trade.get("symbol"),"timestamp":trade.get("timestamp"),"normalized_key":key})
            continue
        matched_trade = {**trade, "context":ctx}
        matched.append(matched_trade)
        dynamic_ctx = dynamic_context_by_ts.get(key)
        if dynamic_ctx:
            dynamic_matched.append({**matched_trade,"dynamic_context":dynamic_ctx})

    baseline = {direction:_summary([x for x in matched if x.get("direction")==direction]) for direction in ("LONG","SHORT")}
    overall = _summary(matched)
    groups = defaultdict(list)
    for trade in matched:
        direction = str(trade.get("direction","UNKNOWN"))
        ctx = trade.get("context") or {}
        for feature in FEATURES:
            value = str(ctx.get(feature,"UNKNOWN"))
            if value == "UNKNOWN":
                continue
            groups[(direction,feature,value)].append(trade)

    rows = []
    for (direction,feature,value), sample in groups.items():
        s = _summary(sample); b = baseline.get(direction) or {"avg_r":0.0,"win_rate":0.0}
        delta_r = round(s["avg_r"]-float(b.get("avg_r",0.0)),3)
        delta_win = round(s["win_rate"]-float(b.get("win_rate",0.0)),1)
        rows.append({
            "label":f"{direction} · {feature}={value}",
            "direction":direction,"feature":feature,"value":value,
            **s,"baseline_avg_r":b.get("avg_r",0.0),"baseline_win_rate":b.get("win_rate",0.0),
            "delta_avg_r":delta_r,"delta_win_rate_pp":delta_win,
            "state":_effect_state(s["trades"],delta_r,delta_win),
        })
    rows.sort(key=lambda x:(abs(x["delta_avg_r"]),x["trades"]), reverse=True)
    v5 = _build_archetype_effects(matched)
    v6 = _build_dynamic_effects(dynamic_matched)
    v6.update({
        "matched_trades":len(dynamic_matched),
        "match_rate_pct":round(len(dynamic_matched)/len(eligible_trades)*100.0,1) if eligible_trades else 0.0,
        "excluded_without_prior_same_day_context":len(matched)-len(dynamic_matched),
        "prior_observation_rule":"Exactly 15 minutes earlier on the same Asia/Kolkata trading day",
    })

    return {
        "mode":"ALPHAPILOT_MARKET_BRAIN_V4_SETUP_EXPECTANCY",
        "research_only":True,"production_rules_changed":False,
        "start_date":start_date,"end_date":end_date,"setup_symbols":SETUP_SYMBOLS,
        "setup_engine":"Existing historical scanner technical/MTF/safety logic; 1 trade per symbol/day maximum",
        "context_observations":len(context_obs),"setup_trades":len(trades),"eligible_setup_trades":len(eligible_trades),"matched_trades":len(matched),
        "match_rate_pct":round(len(matched)/len(eligible_trades)*100.0,1) if eligible_trades else 0.0,
        "overall":overall,"baseline_by_direction":baseline,"effects":rows,
        "boosts":sum(x["state"]=="BOOST" for x in rows),"drags":sum(x["state"]=="DRAG" for x in rows),
        "v5_archetype_context":v5,
        "v6_dynamic_context":v6,
        "context_errors":context_errors,"backtest_errors":backtest.get("errors",[]),
        "match_diagnostics":{
            "timestamp_key":"Asia/Kolkata minute",
            "eligible_context_window":f"{CONTEXT_WINDOW_START}-{CONTEXT_WINDOW_END}",
            "outside_context_window_count":len(outside_window_trades),
            "outside_context_window_samples":[{"symbol":t.get("symbol"),"timestamp":t.get("timestamp")} for t in outside_window_trades[:5]],
            "unmatched_eligible_count":len(unmatched),
            "unmatched_samples":unmatched[:5],
            "context_key_samples":list(context_by_ts.keys())[:5],
        },
        "fixed_effect_rules":{"min_group_trades":MIN_GROUP_OBS,"delta_avg_r":DELTA_R_GATE,"delta_win_rate_pp":DELTA_WIN_GATE},
        "limitations":[
            "This tests whether Market Brain context changes expectancy of existing historical scanner setups; it does not predict unconditional NIFTY direction.",
            "Only scanner setups inside the frozen Market Brain context window (09:45-14:30 IST) are eligible for context matching; early/late setups are reported separately rather than silently counted as failed matches.",
            "P&L uses the existing underlying-price R backtest, not historical option-premium execution.",
            "Context features are the same frozen Market Brain breadth/flow/index-phase/sector-state descriptors.",
            "BOOST/DRAG thresholds are fixed before results and are not production gates.",
            "v5 archetypes are frozen setup-time descriptors: alpha band, scanner R:R band and entry-time band. They do not use future outcome information.",
            "v6 dynamic features use only the setup-time context and the context observation exactly 15 minutes earlier on the same trading day.",
            "News and cross-asset history remain excluded until timestamp-aligned historical feeds are available.",
        ],
    }
