import html
import re
from datetime import datetime, time, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

BULLISH_WORDS={"beats","beat","surge","surges","rally","rallies","gain","gains","upgrade","upgrades","growth","record","approval","approves","profit","profits","strong","bullish","buyback","dividend","order win"}
BEARISH_WORDS={"misses","miss","falls","fall","slump","drops","drop","decline","downgrade","downgrades","loss","losses","weak","bearish","fraud","probe","penalty","default","war","sanction","tariff","lawsuit"}
SYMBOL_ALIASES={"RELIANCE":["reliance industries","reliance"],"TCS":["tata consultancy services","tcs"],"INFY":["infosys","infy"],"HDFCBANK":["hdfc bank","hdfcbank"],"ICICIBANK":["icici bank","icicibank"],"SBIN":["state bank of india","sbi"],"AXISBANK":["axis bank"],"KOTAKBANK":["kotak mahindra bank","kotak bank"],"INDUSINDBK":["indusind bank"],"BAJFINANCE":["bajaj finance"],"BAJAJFINSV":["bajaj finserv"],"LT":["larsen & toubro","larsen and toubro"],"BHARTIARTL":["bharti airtel","airtel"],"ITC":["itc limited","itc"],"HINDUNILVR":["hindustan unilever","hul"],"MARUTI":["maruti suzuki","maruti"],"M&M":["mahindra & mahindra","mahindra and mahindra"],"TATAMOTORS":["tata motors"],"SUNPHARMA":["sun pharma","sun pharmaceutical"],"DRREDDY":["dr reddy","dr. reddy"],"CIPLA":["cipla"],"DIVISLAB":["divi's laboratories","divis laboratories"],"APOLLOHOSP":["apollo hospitals","apollo hospital"],"WIPRO":["wipro"],"HCLTECH":["hcl technologies","hcltech"],"TECHM":["tech mahindra"],"LTIM":["ltimindtree","lti mindtree"],"TITAN":["titan company","titan"],"ASIANPAINT":["asian paints"],"ULTRACEMCO":["ultratech cement","ultratech"],"TATASTEEL":["tata steel"],"JSWSTEEL":["jsw steel"],"HINDALCO":["hindalco"],"COALINDIA":["coal india"],"ONGC":["ongc","oil and natural gas corporation"],"NTPC":["ntpc"],"POWERGRID":["power grid corporation","powergrid"],"ADANIENT":["adani enterprises"],"ADANIPORTS":["adani ports"],"GRASIM":["grasim industries","grasim"],"NESTLEIND":["nestle india"],"BRITANNIA":["britannia industries","britannia"],"EICHERMOT":["eicher motors"],"HEROMOTOCO":["hero motocorp","hero moto"]}

def _clamp(v,low=-10.,high=10.): return max(low,min(high,v))
def _headline_sentiment(title):
    t=title.lower(); return max(-2,min(2,sum(1 for w in BULLISH_WORDS if w in t)-sum(1 for w in BEARISH_WORDS if w in t)))
def _gift_weight():
    now=datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday()>=5:return 1.0,"WEEKEND/NEXT_SESSION"
    if time(9,15)<=now.time()<time(15,15):return .35,"REGULAR_NSE_HOURS"
    return 1.0,"PREOPEN_OR_OVERNIGHT"
def _clean(raw): return html.unescape(re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",raw)))
def _gift_result(ltp,pct,source,manual=False,change=None,expiry=None):
    if not (5000<=float(ltp)<=50000) or abs(float(pct))>10: raise ValueError("GIFT quote failed validation")
    pct=float(pct); raw=_clamp(pct*4.,-6.,6.); weight,regime=_gift_weight(); bias="BULLISH" if pct>=.35 else "BEARISH" if pct<=-.35 else "NEUTRAL"
    return {"status":"AVAILABLE","source":source,"manual":manual,"ltp":float(ltp),"change":change,"change_pct":round(pct,2),"expiry":expiry,"bias":bias,"raw_context_score":round(raw,1),"weight_applied":weight,"weight_regime":regime,"context_score":round(raw*weight,1),"fetched_at":datetime.now(timezone.utc).isoformat(),"warning":"Manual value" if manual else "Public-web context only; not execution-grade."}
def _parse_nseix(text):
    m=re.search(r"Index\s+Futures\s+NIFTY\s+(?P<expiry>\d{1,2}-[A-Za-z]{3}-\d{4})\s+-\s+-\s+(?P<ltp>[0-9,]+(?:\.[0-9]+)?)\s+(?P<change>[+-]?[0-9,]+(?:\.[0-9]+)?)\s+(?P<pct>[+-]?[0-9]+(?:\.[0-9]+)?)",text,re.I)
    if not m: raise ValueError("NIFTY futures quote not parseable on NSE IX")
    return float(m.group("ltp").replace(",","")),float(m.group("pct")),float(m.group("change").replace(",","")),m.group("expiry")
def _parse_investing(raw):
    m1=re.search(r'data-test="instrument-price-last"[^>]*>([0-9,]+(?:\.[0-9]+)?)',raw,re.I); m2=re.search(r'data-test="instrument-price-change-percent"[^>]*>\s*\(?([+-]?[0-9]+(?:\.[0-9]+)?)%',raw,re.I)
    if not m1 or not m2: raise ValueError("Fallback quote failed validation")
    return float(m1.group(1).replace(",","")),float(m2.group(1)),None,None
async def fetch_gift_nifty(manual_gift=None):
    headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36","Accept":"text/html,application/xhtml+xml","Accept-Language":"en-IN,en;q=0.9"}; attempts=[]
    for source,url,parser in [("NSE IX Derivatives Watch","https://www.nseix.com/markets/derivatives-watch",lambda r:_parse_nseix(_clean(r))),("NSE IX Derivatives Watch","https://www1.nseix.com/markets/derivatives-watch",lambda r:_parse_nseix(_clean(r))),("Investing.com fallback","https://in.investing.com/indices/gift-nifty-50-c1-futures",_parse_investing)]:
        try:
            async with httpx.AsyncClient(timeout=12,follow_redirects=True,headers=headers) as c:r=await c.get(url)
            r.raise_for_status(); ltp,pct,change,expiry=parser(r.text); return _gift_result(ltp,pct,source,False,change,expiry)
        except Exception as exc: attempts.append({"source":source,"error":str(exc) or exc.__class__.__name__})
    if manual_gift:
        try:
            ltp=float(manual_gift.get("ltp")); pct=float(manual_gift.get("change_pct")); entered_at=manual_gift.get("entered_at")
            if entered_at:
                dt=datetime.fromisoformat(entered_at.replace("Z","+00:00")); age=(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()/60
                if age>30: raise ValueError("Manual GIFT NIFTY input is older than 30 minutes")
            result=_gift_result(ltp,pct,"MANUAL_GIFT_NIFTY",True); result["entered_at"]=entered_at; result["attempts_before_manual"]=attempts; return result
        except Exception as exc: attempts.append({"source":"MANUAL_GIFT_NIFTY","error":str(exc) or exc.__class__.__name__})
    return {"status":"UNAVAILABLE","source":"NSE IX + fallback","bias":"UNKNOWN","context_score":0.0,"attempts":attempts,"error":"All GIFT NIFTY sources failed validation or were unreachable","fetched_at":datetime.now(timezone.utc).isoformat(),"warning":"No GIFT NIFTY adjustment applied."}
def _relevant(title,symbol):
    t=title.lower(); aliases=[a.lower() for a in SYMBOL_ALIASES.get(symbol,[])]; return any(a in t for a in aliases) or (len(symbol)>=5 and re.search(rf"\b{re.escape(symbol.lower())}\b",t) is not None)
async def fetch_news_context(symbol):
    symbol=symbol.upper().strip(); aliases=SYMBOL_ALIASES.get(symbol,[symbol]); primary=aliases[0]; q=quote_plus(f'"{primary}" stock NSE India when:1d'); url=f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(timeout=12,follow_redirects=True) as c:r=await c.get(url,headers={"User-Agent":"Mozilla/5.0 AlphaPilot/0.9"})
        r.raise_for_status(); root=ET.fromstring(r.text); now=datetime.now(timezone.utc); heads=[]; weighted=weight=0.; discarded=0
        for item in root.findall(".//item"):
            if len(heads)>=8:break
            title=(item.findtext("title") or "").strip()
            if not _relevant(title,symbol): discarded+=1; continue
            source=(item.findtext("source") or "").strip(); pub=(item.findtext("pubDate") or "").strip(); published=None; age=None
            try:
                published=parsedate_to_datetime(pub); published=published if published.tzinfo else published.replace(tzinfo=timezone.utc); age=max(0.,(now-published.astimezone(timezone.utc)).total_seconds()/3600)
            except Exception: pass
            s=_headline_sentiment(title); fresh=1. if age is None else max(.15,1.-min(age,24.)/28.); weighted+=s*fresh; weight+=fresh; heads.append({"title":title,"source":source,"published_at":published.isoformat() if published else pub or None,"age_hours":round(age,1) if age is not None else None,"sentiment":s})
        raw=weighted/weight if weight else 0.; score=round(_clamp(raw*2.,-4.,4.),1); bias="BULLISH" if score>=1.5 else "BEARISH" if score<=-1.5 else "NEUTRAL"
        return {"status":"AVAILABLE" if heads else "NO_RELEVANT_HEADLINES","source":"Google News RSS","symbol":symbol,"query_name":primary,"bias":bias,"context_score":score,"headline_count":len(heads),"discarded_irrelevant":discarded,"headlines":heads,"fetched_at":now.isoformat(),"warning":"Only stock-name-matched headlines are scored. News remains contextual and cannot create a trade by itself."}
    except Exception as exc:return {"status":"UNAVAILABLE","source":"Google News RSS","symbol":symbol,"bias":"UNKNOWN","context_score":0.,"headline_count":0,"headlines":[],"error":str(exc) or exc.__class__.__name__,"fetched_at":datetime.now(timezone.utc).isoformat()}
async def external_market_context(symbol,manual_gift=None):
    gift=await fetch_gift_nifty(manual_gift); news=await fetch_news_context(symbol)
    return {"symbol":symbol.upper(),"gift_nifty":gift,"news":news,"combined_context_adjustment":round(_clamp(float(gift.get("context_score",0))+float(news.get("context_score",0)),-8.,8.),1),"rule":"Context can confirm or penalize an existing setup; it cannot promote NO_TRADE into SETUP."}
