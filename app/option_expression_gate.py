from __future__ import annotations

def option_expression_gate(underlying_setup:dict, option_snapshot:dict|None)->dict:
    direction=underlying_setup.get("direction")
    side="CE" if direction=="BULLISH" else ("PE" if direction=="BEARISH" else None)
    if not side:return {"action":"NO_TRADE","reason":"NO_UNDERLYING_DIRECTION"}
    if not option_snapshot:
        return {"action":"UNDERLYING_SETUP_ONLY","option_side":side,"reason":"GENUINE_OPTION_DATA_UNAVAILABLE",
                "rule":"Never fabricate strike, premium, IV, Greeks, spread, or option P&L."}
    candidates=[x for x in option_snapshot.get("contracts",[]) if str(x.get("option_type","")).upper()==side]
    viable=[]
    for x in candidates:
        bid=x.get("bid");ask=x.get("ask");ltp=x.get("ltp")
        if ltp is None:continue
        if bid is not None and ask is not None and float(ask)<float(bid):continue
        spread_pct=None
        if bid is not None and ask is not None and float(ltp)>0:spread_pct=(float(ask)-float(bid))/float(ltp)*100
        y=dict(x);y["spread_pct"]=round(spread_pct,2) if spread_pct is not None else None
        if spread_pct is not None and spread_pct>8:continue
        viable.append(y)
    if not viable:return {"action":"NO_TRADE","option_side":side,"reason":"NO_EXECUTABLE_OPTION_CONTRACT"}
    return {"action":"OPTION_CANDIDATES","option_side":side,"contracts":viable,
            "selection_rule":"Expose executable candidates; strike/expiry ranking requires explicit liquidity, expiry, moneyness and risk policy.",
            "rule":"Option Brain expresses an already-valid underlying thesis; it does not manufacture direction."}
