from __future__ import annotations
from datetime import datetime, time as dt_time
from statistics import mean, median, pstdev
from typing import Any

def f(v, d=0.0):
    try: return d if v is None else float(v)
    except (TypeError, ValueError): return d

def clean_candles(candles):
    out=[]
    for c in candles:
        if not isinstance(c,(list,tuple)) or len(c)<6 or any(x is None for x in c[1:5]): continue
        ts,o,h,l,cl,vol=c[:6]
        o,h,l,cl,vol=f(o),f(h),f(l),f(cl),max(0,f(vol))
        if min(o,h,l,cl)<=0 or h<l: continue
        try:
            t=datetime.fromisoformat(str(ts)).time()
            if t<dt_time(9,15) or t>dt_time(15,30): continue
        except Exception: pass
        out.append([ts,o,h,l,cl,vol,c[6] if len(c)>6 else None])
    return out

def ema_series(v,p):
    if not v:return []
    k=2/(p+1); out=[v[0]]
    for x in v[1:]: out.append(x*k+out[-1]*(1-k))
    return out

def ema(v,p):
    s=ema_series(v,p); return s[-1] if s else 0

def rsi(v,p=14):
    if len(v)<=p:return 50
    g=[]; loss=[]
    for i in range(1,len(v)):
        x=v[i]-v[i-1]; g.append(max(x,0)); loss.append(max(-x,0))
    ag=mean(g[-p:]); al=mean(loss[-p:])
    return 100 if al==0 else 100-100/(1+ag/al)

def atr(c,p=14):
    if len(c)<2:return 0
    tr=[]
    for i in range(1,len(c)):
        h,l,pc=f(c[i][2]),f(c[i][3]),f(c[i-1][4])
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    return mean(tr[-p:]) if tr else 0

def macd(v):
    a=ema_series(v,12); b=ema_series(v,26); n=min(len(a),len(b))
    line=[a[-n+i]-b[-n+i] for i in range(n)] if n else []
    sig=ema_series(line,9)
    ml=line[-1] if line else 0; sl=sig[-1] if sig else 0
    return ml,sl,ml-sl

def boll(v,p=20):
    w=v[-p:]; m=mean(w); sd=pstdev(w) if len(w)>1 else 0
    return m+2*sd,m,m-2*sd

def session_vwap(c):
    if not c:return 0
    last_date=str(c[-1][0])[:10]
    s=[x for x in c if str(x[0])[:10]==last_date]
    pv=sum(((x[2]+x[3]+x[4])/3)*x[5] for x in s); vv=sum(x[5] for x in s)
    return pv/vv if vv else c[-1][4]

def volume_ratio_same_slot(c):
    if not c:return 1.0,1.0
    latest=c[-1]; slot=str(latest[0])[11:16]
    peers=[x[5] for x in c[:-1] if str(x[0])[11:16]==slot and x[5]>0][-20:]
    if len(peers)<3: peers=[x[5] for x in c[-21:-1] if x[5]>0]
    baseline=median(peers) if peers else 0
    raw=latest[5]/baseline if baseline else 1
    return raw,min(raw,3.0)

def structure(c,n=20):
    r=c[-n:]; sup=min(x[3] for x in r); res=max(x[2] for x in r)
    if len(r)<6:return "RANGE",sup,res
    a=r[:len(r)//2]; b=r[len(r)//2:]
    ah,al=max(x[2] for x in a),min(x[3] for x in a)
    bh,bl=max(x[2] for x in b),min(x[3] for x in b)
    if bh>ah and bl>al:return "UPTREND",sup,res
    if bh<ah and bl<al:return "DOWNTREND",sup,res
    return "RANGE",sup,res

def candle_pattern(c):
    if len(c)<2:return "NONE"
    p,x=c[-2],c[-1]; po,pc=p[1],p[4]; o,h,l,cl=x[1],x[2],x[3],x[4]
    body=abs(cl-o); rng=max(h-l,1e-9); up=h-max(o,cl); low=min(o,cl)-l
    if cl>o and pc<po and o<=pc and cl>=po:return "BULLISH_ENGULFING"
    if cl<o and pc>po and o>=pc and cl<=po:return "BEARISH_ENGULFING"
    if body/rng<=.1:return "DOJI"
    if low>body*2 and up<=body:return "HAMMER"
    if up>body*2 and low<=body:return "SHOOTING_STAR"
    return "NONE"

def analyze_candles(symbol,candles,min_rr=1.5):
    c=clean_candles(candles)
    if len(c)<60:return {"symbol":symbol,"status":"NO_TRADE","reason":"Not enough clean history"}
    closes=[x[4] for x in c]; last=closes[-1]
    e9,e20,e50=ema(closes,9),ema(closes,20),ema(closes,50)
    e200=ema(closes,200) if len(closes)>=200 else None
    r=rsi(closes); a=atr(c); ml,ms,mh=macd(closes); bu,bm,bl=boll(closes)
    vw=session_vwap(c); rawvr,vr=volume_ratio_same_slot(c)
    st,sup,res=structure(c); pat=candle_pattern(c)
    dist_res=(res-last)/a if a else 0; dist_sup=(last-sup)/a if a else 0
    trend=momentum=struct=volume=volatility=price_action=0
    reasons=[]; warnings=[]
    if last>e20>e50: trend+=12; reasons.append("EMA20/50 trend aligned bullish")
    elif last<e20<e50: trend-=12; reasons.append("EMA20/50 trend aligned bearish")
    elif e20<e50: warnings.append("EMA20 remains below EMA50")
    if e9>e20: trend+=4
    else: trend-=4
    if last>vw: trend+=4; reasons.append("Above session VWAP")
    else: trend-=4
    if e200 is not None:
        if last>e200: trend+=5
        else: trend-=5
    if 55<=r<=70: momentum+=6; reasons.append("RSI bullish")
    elif 30<=r<=45: momentum-=6
    if mh>0: momentum+=8; reasons.append("MACD histogram positive")
    elif mh<0: momentum-=8; warnings.append("MACD momentum conflicts with bullish bias")
    roc=((last/closes[-11])-1)*100 if len(closes)>10 else 0
    if roc>.25: momentum+=6
    elif roc<-.25: momentum-=6
    if st=="UPTREND": struct+=12; reasons.append("Higher-high / higher-low structure")
    elif st=="DOWNTREND": struct-=12
    if 0<=dist_res<0.75: struct-=5; warnings.append("Entry is close to resistance")
    if 0<=dist_sup<0.75: struct+=3
    if vr>=2: volume+=10; reasons.append("Strong time-normalized volume")
    elif vr>=1.25: volume+=7
    elif vr>=.8: volume+=3
    elif vr<.5: volume-=7; warnings.append("Weak time-normalized volume")
    if rawvr>5: warnings.append("Abnormal raw volume spike capped")
    atrpct=a/last*100 if last else 0
    if .15<=atrpct<=1.5: volatility+=5
    width=(bu-bl)/bm*100 if bm else 0
    if .5<=width<=5: volatility+=3
    elif width<.5: warnings.append("Volatility compression")
    if pat in ("BULLISH_ENGULFING","HAMMER"): price_action+=6
    elif pat in ("BEARISH_ENGULFING","SHOOTING_STAR"): price_action-=6
    elif pat=="DOJI": price_action-=4; warnings.append("Doji signals indecision")
    max_abs=25+20+20+10+8+8
    bias=trend+momentum+struct+volume+volatility+price_action
    alpha=max(0,min(100,50+(bias/max_abs)*50))
    long_conf=sum([trend>=8,momentum>=4,struct>=7,volume>=3,price_action>0])
    short_conf=sum([trend<=-8,momentum<=-4,struct<=-7,price_action<0])
    if alpha>=68 and long_conf>=3 and momentum>-8: direction="LONG"
    elif alpha<=32 and short_conf>=3 and momentum<8: direction="SHORT"
    else: direction="NO_TRADE"
    if alpha>=85 and long_conf>=4 and momentum>=8: label="STRONG_LONG"
    elif direction=="LONG": label="LONG"
    elif alpha<=15 and short_conf>=4 and momentum<=-8: label="STRONG_SHORT"
    elif direction=="SHORT": label="SHORT"
    elif alpha>=58: label="WATCH_LONG"
    elif alpha<=42: label="WATCH_SHORT"
    else: label="NO_TRADE"
    base={
      "symbol":symbol,"alpha_score":round(alpha,1),"signal":label,"price":round(last,2),
      "latest_candle_at":str(c[-1][0]),
      "family_scores":{"trend":trend,"momentum":momentum,"structure":struct,"volume":volume,"volatility":volatility,"price_action":price_action},
      "ema9":round(e9,2),"ema20":round(e20,2),"ema50":round(e50,2),"ema200":round(e200,2) if e200 is not None else None,"vwap":round(vw,2),
      "rsi14":round(r,2),"macd":round(ml,4),"macd_signal":round(ms,4),"macd_hist":round(mh,4),"atr14":round(a,2),
      "bollinger_upper":round(bu,2),"bollinger_mid":round(bm,2),"bollinger_lower":round(bl,2),"volume_ratio_raw":round(rawvr,2),"volume_ratio_capped":round(vr,2),
      "market_structure":st,"recent_support":round(sup,2),"recent_resistance":round(res,2),"distance_to_resistance_atr":round(dist_res,2),"distance_to_support_atr":round(dist_sup,2),
      "candle_pattern":pat,"confirmations":{"long":long_conf,"short":short_conf},"reasons":reasons,"warnings":warnings,"clean_candles":len(c)
    }
    if direction=="NO_TRADE": return {**base,"status":"NO_TRADE","reason":"Confluence threshold not met"}
    risk=max(a*1.25,last*.003)
    if direction=="LONG":
        stop=min(last-risk,sup-.15*a) if sup<last else last-risk; rrisk=last-stop; t1=last+rrisk*min_rr; t2=last+rrisk*max(2,min_rr+.5)
    else:
        stop=max(last+risk,res+.15*a) if res>last else last+risk; rrisk=stop-last; t1=last-rrisk*min_rr; t2=last-rrisk*max(2,min_rr+.5)
    return {**base,"status":"SETUP","direction":direction,"entry":round(last,2),"stop_loss":round(stop,2),"target1":round(t1,2),"target2":round(t2,2),"risk_reward":round(min_rr,2),"note":"Alpha Score is a normalized confluence score, not probability of profit."}
