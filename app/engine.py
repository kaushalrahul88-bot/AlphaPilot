from statistics import mean

def ema(values, period):
    if not values: return 0.0
    k=2/(period+1); result=values[0]
    for value in values[1:]: result=value*k+result*(1-k)
    return result

def rsi(values, period=14):
    if len(values)<=period: return 50.0
    gains=[]; losses=[]
    for i in range(1,len(values)):
        ch=values[i]-values[i-1]; gains.append(max(ch,0.0)); losses.append(max(-ch,0.0))
    ag=mean(gains[-period:]); al=mean(losses[-period:])
    if al==0: return 100.0
    rs=ag/al; return 100-(100/(1+rs))

def atr(candles, period=14):
    if len(candles)<2: return 0.0
    trs=[]
    for i in range(1,len(candles)):
        h=float(candles[i][2]); l=float(candles[i][3]); pc=float(candles[i-1][4])
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    return mean(trs[-period:]) if trs else 0.0

def analyze_candles(symbol, candles, min_rr=1.5):
    if len(candles)<30: return {"symbol":symbol,"status":"NO_TRADE","reason":"Not enough candle history"}
    closes=[float(c[4]) for c in candles]; highs=[float(c[2]) for c in candles]; lows=[float(c[3]) for c in candles]; volumes=[float(c[5] or 0) for c in candles]
    last=closes[-1]; e9=ema(closes[-60:],9); e21=ema(closes[-60:],21); r14=rsi(closes[-40:],14); a14=atr(candles[-40:],14)
    rh=max(highs[-20:]); rl=min(lows[-20:]); av=mean(volumes[-20:]) if any(volumes[-20:]) else 0.0; vr=volumes[-1]/av if av>0 else 1.0
    bullish=e9>e21 and last>=e9; bearish=e9<e21 and last<=e9; score=50; reasons=[]
    if bullish: score+=18; reasons.append("EMA trend bullish")
    elif bearish: score-=18; reasons.append("EMA trend bearish")
    if r14>=55: score+=10; reasons.append("RSI confirms bullish momentum")
    elif r14<=45: score-=10; reasons.append("RSI confirms bearish momentum")
    if vr>=1.2:
        if bullish: score+=8
        elif bearish: score-=8
        reasons.append("Volume expansion")
    if bullish and last>=rh-0.25*max(a14,0.01): score+=8; reasons.append("Price pressing recent resistance")
    if bearish and last<=rl+0.25*max(a14,0.01): score-=8; reasons.append("Price pressing recent support")
    direction="LONG" if score>=65 else "SHORT" if score<=35 else "NO_TRADE"
    confidence=min(100,max(0,score if direction!="SHORT" else 100-score))
    base={"symbol":symbol,"alpha_score":round(confidence,1),"price":round(last,2),"ema9":round(e9,2),"ema21":round(e21,2),"rsi14":round(r14,2),"atr14":round(a14,2),"volume_ratio":round(vr,2),"recent_support":round(rl,2),"recent_resistance":round(rh,2),"reasons":reasons}
    if direction=="NO_TRADE" or a14<=0: return {**base,"status":"NO_TRADE"}
    risk=max(a14*1.2,last*0.003)
    if direction=="LONG": stop=last-risk; t1=last+risk*min_rr; t2=last+risk*max(2.0,min_rr+0.5)
    else: stop=last+risk; t1=last-risk*min_rr; t2=last-risk*max(2.0,min_rr+0.5)
    return {**base,"status":"SETUP","direction":direction,"entry":round(last,2),"stop_loss":round(stop,2),"target1":round(t1,2),"target2":round(t2,2),"risk_reward":round(min_rr,2),"note":"Research signal only. Validate through backtesting and paper trading before real-money use."}
