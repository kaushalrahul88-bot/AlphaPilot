from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from .backtest import _historical

SECTOR = {
    "NIFTY":"INDEX","BANKNIFTY":"INDEX","HDFCBANK":"FINANCIALS","ICICIBANK":"FINANCIALS","SBIN":"FINANCIALS","AXISBANK":"FINANCIALS","KOTAKBANK":"FINANCIALS","BAJFINANCE":"FINANCIALS",
    "TCS":"IT","INFY":"IT","HCLTECH":"IT","WIPRO":"IT","RELIANCE":"ENERGY","ONGC":"ENERGY","NTPC":"POWER","POWERGRID":"POWER",
    "TATASTEEL":"METALS","JSWSTEEL":"METALS","HINDALCO":"METALS","MARUTI":"AUTO","M&M":"AUTO","TATAMOTORS":"AUTO","SUNPHARMA":"PHARMA","DRREDDY":"PHARMA","CIPLA":"PHARMA",
    "ITC":"CONSUMER","TITAN":"CONSUMER","ASIANPAINT":"CONSUMER","LT":"INDUSTRIALS","ADANIPORTS":"INDUSTRIALS","ULTRACEMCO":"MATERIALS","BHARTIARTL":"TELECOM",
}
SYMBOLS = list(SECTOR)
STOCKS = [s for s in SYMBOLS if s not in {"NIFTY","BANKNIFTY"}]
FEATURES = ("breadth","flow","niftyPhase","bankPhase","leaders","financials","it","metals")


def _n(v, d=0.0):
    try: return float(v)
    except Exception: return d

def _day(ts): return str(ts)[:10]
def _slot(ts): return str(ts)[11:16]

def _phase(change, trend):
    if change > .05 and trend == "UP": return "ALIGNED_UP"
    if change < -.05 and trend == "DOWN": return "ALIGNED_DOWN"
    if change < -.05 and trend == "UP": return "RECOVERY"
    if change > .05 and trend == "DOWN": return "FADE"
    return "MIXED"


def _summarize_at(symbol, rows, i):
    if i < 6 or i >= len(rows): return None
    last = rows[i]; last_day = _day(last[0])
    window = rows[:i+1]
    session = [x for x in window if _day(x[0]) == last_day]
    prior = [x for x in window if _day(x[0]) < last_day]
    if not session or not prior: return None
    close = _n(last[4]); prior_close = _n(prior[-1][4])
    if not close or not prior_close: return None
    change = (close / prior_close - 1) * 100
    vv = sum(_n(x[5]) for x in session)
    pv = sum(((_n(x[2])+_n(x[3])+_n(x[4]))/3) * _n(x[5]) for x in session)
    vwap = pv / vv if vv else close
    current_slot = _slot(last[0])
    peer = [_n(x[5]) for x in prior if _slot(x[0]) == current_slot and _n(x[5]) > 0][-10:]
    fallback = [_n(x[5]) for x in rows[max(0, i-24):i] if _n(x[5]) > 0]
    base = peer if len(peer) >= 3 else fallback
    avg = sum(base)/len(base) if base else 0
    vr = _n(last[5])/avg if avg else 1
    look = _n(session[-6][4]) if len(session) > 5 else _n(session[0][1])
    trend = "UP" if close > look else "DOWN" if close < look else "FLAT"
    return {"symbol":symbol,"sector":SECTOR.get(symbol,"OTHER"),"change":change,"above":close>=vwap,"vr":vr,"trend":trend,"phase":_phase(change,trend)}


def _sector_state(rows, sector):
    a = [x for x in rows if x["sector"] == sector]
    if not a: return "UNKNOWN"
    adv = sum(x["change"] > .05 for x in a); dec = sum(x["change"] < -.05 for x in a); above = sum(x["above"] for x in a)
    score = ((adv-dec)/len(a)*50) + ((above/len(a))-.5)*50
    return "LEADING" if score >= 20 else "LAGGING" if score <= -20 else "MIXED"


def _build(all_rows):
    nifty = all_rows.get("NIFTY", []); bank = all_rows.get("BANKNIFTY", [])
    maps = {s:{str(x[0]):i for i,x in enumerate(all_rows.get(s, []))} for s in SYMBOLS}
    out = []
    for i in range(8, len(nifty)-4):
        ts = str(nifty[i][0]); sl = _slot(ts)
        if sl < "09:45" or sl > "14:30": continue
        rows = []
        for s in STOCKS:
            j = maps[s].get(ts)
            if j is None: continue
            r = _summarize_at(s, all_rows[s], j)
            if r: rows.append(r)
        if len(rows) < 24: continue
        ni = _summarize_at("NIFTY", nifty, i)
        bj = maps["BANKNIFTY"].get(ts); br = _summarize_at("BANKNIFTY", bank, bj) if bj is not None else None
        if not ni or not br: continue
        adv = sum(x["change"] > .05 for x in rows); dec = sum(x["change"] < -.05 for x in rows); above = sum(x["above"] for x in rows)
        bscore = ((adv-dec)/len(rows)*50) + ((above/len(rows))-.5)*50
        breadth = "BROAD_RISK_ON" if bscore >= 20 else "BROAD_RISK_OFF" if bscore <= -20 else "MIXED"
        fscore = sum((1 if x["change"]>.05 else -1 if x["change"]<-.05 else 0)*min(x["vr"],2) for x in rows)/len(rows)*25
        flow = "BUYING_PRESSURE" if fscore >= 15 else "SELLING_PRESSURE" if fscore <= -15 else "BALANCED"
        sectors = sorted({x["sector"] for x in rows}); lead = sum(_sector_state(rows,s)=="LEADING" for s in sectors)
        leaders = "6+_LEADERS" if lead >= 6 else "3-5_LEADERS" if lead >= 3 else "0-2_LEADERS"
        c = _n(nifty[i][4])
        out.append({
            "ts":ts,"breadth":breadth,"flow":flow,"niftyPhase":ni["phase"],"bankPhase":br["phase"],"leaders":leaders,
            "financials":_sector_state(rows,"FINANCIALS"),"it":_sector_state(rows,"IT"),"metals":_sector_state(rows,"METALS"),
            "fwd15":(_n(nifty[i+1][4])/c-1)*100,"fwd30":(_n(nifty[i+2][4])/c-1)*100,"fwd60":(_n(nifty[i+4][4])/c-1)*100,
        })
    return out


def _pairs(items):
    for i in range(len(items)):
        for j in range(i+1, len(items)):
            yield items[i], items[j]


def _summaries(obs, min_obs=20):
    base = sum(x["fwd60"] for x in obs)/len(obs) if obs else 0
    out = []
    for a,b in _pairs(FEATURES):
        groups = defaultdict(list)
        for o in obs:
            va,vb = str(o[a]),str(o[b])
            if "UNKNOWN" in {va,vb}: continue
            groups[(va,vb)].append(o)
        for (va,vb), sample in groups.items():
            if len(sample) < min_obs: continue
            avg = lambda k: sum(x[k] for x in sample)/len(sample)
            avg60 = avg("fwd60"); pos = sum(x["fwd60"]>.15 for x in sample)/len(sample)*100; neg = sum(x["fwd60"]<-.15 for x in sample)/len(sample)*100
            state = "LOW_SAMPLE" if len(sample)<30 else "BULLISH_LEAN" if avg60>=.12 and pos>=55 else "BEARISH_LEAN" if avg60<=-.12 and neg>=55 else "MIXED"
            out.append({"label":f"{a}={va} × {b}={vb}","observations":len(sample),"avg15":round(avg("fwd15"),4),"avg30":round(avg("fwd30"),4),"avg60":round(avg60,4),"lift60":round(avg60-base,4),"positive60_pct":round(pos,1),"negative60_pct":round(neg,1),"state":state})
    return sorted(out, key=lambda x:(abs(x["avg60"]),x["observations"]), reverse=True)


async def run_market_brain_context_block(provider, start_date: str, end_date: str, min_obs: int = 20):
    start = datetime.fromisoformat(start_date); end = datetime.fromisoformat(end_date) + timedelta(hours=23,minutes=59)
    if end < start: raise ValueError("end_date must be on or after start_date")
    if (end-start).days > 16: raise ValueError("Market Brain v3.1 blocks are limited to 16 calendar days")
    data, errors = {}, []
    async def fetch_one(symbol):
        try: return symbol, await _historical(provider, symbol, "15m", start, end), None
        except Exception as exc: return symbol, [], f"{exc.__class__.__name__}: {exc}"
    for i in range(0, len(SYMBOLS), 4):
        batch = await asyncio.gather(*(fetch_one(s) for s in SYMBOLS[i:i+4]))
        for symbol, rows, error in batch:
            data[symbol] = rows
            if error: errors.append({"symbol":symbol,"error":error})
        await asyncio.sleep(.15)
    obs = _build(data); summaries = _summaries(obs, max(20,min(int(min_obs),100)))
    return {
        "mode":"ALPHAPILOT_MARKET_BRAIN_V3_1_BLOCK","research_only":True,"production_rules_changed":False,
        "start_date":start_date,"end_date":end_date,"observations":len(obs),"symbols_available":sum(bool(data.get(s)) for s in SYMBOLS),"symbols_total":len(SYMBOLS),
        "baseline_avg60":round(sum(x["fwd60"] for x in obs)/len(obs),4) if obs else 0.0,
        "bullish_leans":sum(x["state"]=="BULLISH_LEAN" for x in summaries),"bearish_leans":sum(x["state"]=="BEARISH_LEAN" for x in summaries),
        "summaries":summaries,"errors":errors,
        "fixed_rules":{"min_group_obs":max(20,min(int(min_obs),100)),"lean_min_obs":30,"abs_avg60_pct":0.12,"directional_follow_through_pct":55.0,"directional_move_threshold_pct":0.15},
        "limitations":["Same frozen 30-stock proxy and pairwise states as Market Brain v3.","No cross-asset/news history is injected without timestamp-aligned data.","Replication blocks do not alter production or nominate a trade by themselves."]
    }
