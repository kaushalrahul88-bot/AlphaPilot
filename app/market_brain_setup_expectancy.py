from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from .backtest import _historical, run_backtest
from .market_brain_context_research import SYMBOLS, _build

SETUP_SYMBOLS = ["RELIANCE","HDFCBANK","ICICIBANK","SBIN","TCS","INFY","TATASTEEL","MARUTI"]
FEATURES = ("breadth","flow","niftyPhase","bankPhase","leaders","financials","it","metals")
MIN_GROUP_OBS = 12
DELTA_R_GATE = 0.20
DELTA_WIN_GATE = 8.0


def _summary(sample):
    n = len(sample)
    if not n:
        return {"trades":0,"avg_r":0.0,"win_rate":0.0,"total_r":0.0}
    total = sum(float(x.get("r_multiple",0.0)) for x in sample)
    wins = sum(float(x.get("r_multiple",0.0)) > 0 for x in sample)
    return {"trades":n,"avg_r":round(total/n,3),"win_rate":round(wins/n*100.0,1),"total_r":round(total,2)}


def _effect_state(n, delta_r, delta_win):
    if n < MIN_GROUP_OBS:
        return "LOW_SAMPLE"
    if delta_r >= DELTA_R_GATE and delta_win >= DELTA_WIN_GATE:
        return "BOOST"
    if delta_r <= -DELTA_R_GATE and delta_win <= -DELTA_WIN_GATE:
        return "DRAG"
    return "MIXED"


async def run_market_brain_setup_expectancy(provider, start_date: str, end_date: str):
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end-start).days > 16:
        raise ValueError("Market Brain v4 blocks are limited to 16 calendar days")

    context_data, context_errors = {}, []
    async def fetch_one(symbol):
        try:
            rows = await _historical(provider, symbol, "15m", start, end)
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

    context_obs = _build(context_data)
    context_by_ts = {str(x.get("ts")): x for x in context_obs}

    backtest = await run_backtest(provider, SETUP_SYMBOLS, start_date, end_date, 1.5, None)
    trades = backtest.get("trades", [])
    matched = []
    for trade in trades:
        ctx = context_by_ts.get(str(trade.get("timestamp")))
        if not ctx:
            continue
        matched.append({**trade, "context":ctx})

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

    return {
        "mode":"ALPHAPILOT_MARKET_BRAIN_V4_SETUP_EXPECTANCY",
        "research_only":True,"production_rules_changed":False,
        "start_date":start_date,"end_date":end_date,"setup_symbols":SETUP_SYMBOLS,
        "setup_engine":"Existing historical scanner technical/MTF/safety logic; 1 trade per symbol/day maximum",
        "context_observations":len(context_obs),"setup_trades":len(trades),"matched_trades":len(matched),
        "overall":overall,"baseline_by_direction":baseline,"effects":rows,
        "boosts":sum(x["state"]=="BOOST" for x in rows),"drags":sum(x["state"]=="DRAG" for x in rows),
        "context_errors":context_errors,"backtest_errors":backtest.get("errors",[]),
        "fixed_effect_rules":{"min_group_trades":MIN_GROUP_OBS,"delta_avg_r":DELTA_R_GATE,"delta_win_rate_pp":DELTA_WIN_GATE},
        "limitations":[
            "This tests whether Market Brain context changes expectancy of existing historical scanner setups; it does not predict unconditional NIFTY direction.",
            "P&L uses the existing underlying-price R backtest, not historical option-premium execution.",
            "Context features are the same frozen Market Brain breadth/flow/index-phase/sector-state descriptors.",
            "BOOST/DRAG thresholds are fixed before results and are not production gates.",
            "News and cross-asset history remain excluded until timestamp-aligned historical feeds are available.",
        ],
    }
