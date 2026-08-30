from __future__ import annotations

def rank_option_candidates(contracts:list[dict], *, underlying_price:float, expiry_date:str|None=None)->dict:
    """Rank genuine option snapshots for execution quality, not for predicted profitability."""
    scored=[]
    for c in contracts:
        strike=c.get("strike");ltp=c.get("ltp")
        if strike is None or ltp is None or float(ltp)<=0:continue
        bid=c.get("bid");ask=c.get("ask");oi=c.get("oi");volume=c.get("volume")
        spread_pct=None
        if bid is not None and ask is not None:
            spread_pct=max(0.0,(float(ask)-float(bid))/float(ltp)*100)
        moneyness_pct=abs(float(strike)-float(underlying_price))/float(underlying_price)*100
        liquidity_parts=[]
        if spread_pct is not None:liquidity_parts.append(max(0.0,100.0-spread_pct*8))
        if volume is not None:liquidity_parts.append(min(100.0,float(volume)/10))
        if oi is not None:liquidity_parts.append(min(100.0,float(oi)/50))
        liquidity=sum(liquidity_parts)/len(liquidity_parts) if liquidity_parts else None
        score=(liquidity or 0)-min(moneyness_pct,10)*3
        x=dict(c);x.update({"spread_pct":round(spread_pct,2) if spread_pct is not None else None,
                           "moneyness_distance_pct":round(moneyness_pct,3),
                           "execution_quality_score":round(score,2)})
        scored.append(x)
    scored.sort(key=lambda x:x["execution_quality_score"],reverse=True)
    return {"mode":"OPTION_CONTRACT_SELECTOR_V1","expiry_date":expiry_date,"ranked":scored,
      "rule":"Rank only genuine quoted contracts for executability/liquidity and proximity. Do not infer which option will make the most money."}

def select_option(contracts:list[dict], *, underlying_price:float, expiry_date:str|None=None):
    r=rank_option_candidates(contracts,underlying_price=underlying_price,expiry_date=expiry_date)
    if not r["ranked"]:return {"status":"NO_TRADE","reason":"NO_RANKABLE_CONTRACT"}
    top=r["ranked"][0]
    if top.get("spread_pct") is not None and top["spread_pct"]>8:return {"status":"NO_TRADE","reason":"SPREAD_TOO_WIDE"}
    return {"status":"SELECTED","contract":top,"basis":"EXECUTION_QUALITY_AND_MONEYNESS_NOT_PROFIT_FORECAST"}
