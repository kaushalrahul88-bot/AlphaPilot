from __future__ import annotations
from statistics import mean
from .commodity_time import parse_ist_timestamp
from .copper_experience_memory import build_experiences, query_memory, DEFAULT_K
from .copper_market_brain_direction_audit import PRIMARY_END,PRIMARY_START,REFERENCE_CONTRACT


def _choose(memory,min_resolved=15,min_gap=10.0):
    if memory.get("status")!="READY":return "NO_TRADE"
    d=memory["by_direction"]; bull=d["BULLISH"];bear=d["BEARISH"]
    if min(bull["resolved"],bear["resolved"])<min_resolved:return "NO_TRADE"
    bp=bull["target_first_pct_resolved"];sp=bear["target_first_pct_resolved"]
    if bp is None or sp is None or abs(bp-sp)<min_gap:return "NO_TRADE"
    return "BUY" if bp>sp else "SELL"


def evaluate_memory_evidence(candles,sample_every_bars=3,k=DEFAULT_K):
    experiences=build_experiences(candles,sample_every_bars)
    bullish={e["timestamp"]:e for e in experiences if e["direction"]=="BULLISH"}
    bearish={e["timestamp"]:e for e in experiences if e["direction"]=="BEARISH"}
    obs=[]
    for ts in sorted(bullish):
        e=bullish[ts]
        q={x:e[x] for x in ("timestamp","vector","structure","opening_range_break","price_oi_state")}
        mem=query_memory(experiences,q,k)
        signal=_choose(mem)
        if signal=="NO_TRADE":continue
        actual=bullish[ts] if signal=="BUY" else bearish[ts]
        obs.append({"timestamp":ts,"signal":signal,"outcome":actual["outcome"],
                    "minutes_to_event":actual["minutes_to_event"],
                    "bullish_evidence":mem["by_direction"]["BULLISH"]["target_first_pct_resolved"],
                    "bearish_evidence":mem["by_direction"]["BEARISH"]["target_first_pct_resolved"]})
    resolved=[x for x in obs if x["outcome"]!="SESSION_END_NO_EVENT"]
    wins=[x for x in resolved if x["outcome"]=="TARGET_FIRST"]
    def side(sig):
        r=[x for x in resolved if x["signal"]==sig];w=[x for x in r if x["outcome"]=="TARGET_FIRST"]
        return {"resolved":len(r),"target_first":len(w),"target_first_pct":round(len(w)/len(r)*100,2) if r else None}
    return {"mode":"COPPER_MEMORY_EVIDENCE_AUDIT_V1","research_only":True,"production_rules_changed":False,
            "selection_rule":"Choose bullish/CE or bearish/PE only when both analogue directions have >=15 resolved examples and target-first evidence differs by >=10 percentage points.",
            "setups":len(obs),"resolved":len(resolved),"target_first":len(wins),
            "target_first_pct_resolved":round(len(wins)/len(resolved)*100,2) if resolved else None,
            "BUY_CE":side("BUY"),"SELL_PE":side("SELL"),"observations":obs,
            "guardrails":["Selection thresholds were declared before this audit result.","Every analogue query uses prior timestamps only.","NO_TRADE is allowed when evidence is weak.","No option P&L or futures P&L is calculated."]}


async def run_memory_evidence_from_store(store,sample_every_bars=3,k=DEFAULT_K):
    await store.initialize()
    segs=await store.read_symbol_contract_segments("COPPER",5,PRIMARY_START,PRIMARY_END)
    target=next((s for s in segs if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT),None)
    if not target:raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    report=evaluate_memory_evidence(target.get("candles") or [],sample_every_bars,k)
    report["reference_contract"]={"trading_symbol":target.get("trading_symbol"),"candles":len(target.get("candles") or [])}
    return report
