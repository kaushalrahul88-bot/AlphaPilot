from __future__ import annotations

def review_setup_risk(setup:dict, market:dict)->dict:
    reasons=[];warnings=[]
    entry=setup.get("entry_price");stop=setup.get("stop_price");target=setup.get("target_price")
    if entry is None or stop is None or target is None:
        return {"status":"WAIT","reason":"PRICE_LEVELS_NOT_DEFINED","reasons":["entry/stop/target required"]}
    entry=float(entry);stop=float(stop);target=float(target)
    risk=abs(entry-stop);reward=abs(target-entry)
    if risk<=0:return {"status":"NO_TRADE","reason":"INVALID_RISK"}
    rr=reward/risk
    direction=setup.get("direction")
    if direction=="BULLISH" and not (stop<entry<target):reasons.append("LEVEL_ORDER_CONTRADICTS_BULLISH_THESIS")
    if direction=="BEARISH" and not (target<entry<stop):reasons.append("LEVEL_ORDER_CONTRADICTS_BEARISH_THESIS")
    if rr<1.5:reasons.append("INSUFFICIENT_REWARD_RISK")
    if market.get("location") in {"EXTENDED_ABOVE_VALUE","EXTENDED_BELOW_VALUE"}:warnings.append("EXTENDED_LOCATION")
    if market.get("volatility_regime")=="HIGH":warnings.append("HIGH_VOLATILITY_POSITION_RISK")
    if market.get("liquidity")=="POOR":reasons.append("POOR_EXECUTION_LIQUIDITY")
    return {"status":"NO_TRADE" if reasons else "PASS_TO_OPTION_BRAIN","reason":";".join(reasons) if reasons else "RISK_DEFINED_SETUP",
            "risk_points":risk,"reward_points":reward,"reward_risk":round(rr,2),"reasons":reasons,"warnings":warnings,
            "rule":"Risk review can reject or constrain a thesis; it never upgrades weak evidence into a trade."}
