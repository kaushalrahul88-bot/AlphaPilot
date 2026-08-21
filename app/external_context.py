import html
import re
from datetime import datetime, time, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx


BULLISH_WORDS = {"beats","beat","surge","surges","rally","rallies","gain","gains","upgrade","upgrades","growth","record","approval","approves","profit","profits","strong","bullish","buyback","dividend","order win"}
BEARISH_WORDS = {"misses","miss","falls","fall","slump","drops","drop","decline","downgrade","downgrades","loss","losses","weak","bearish","fraud","probe","penalty","default","war","sanction","tariff","lawsuit"}
SYMBOL_ALIASES = {
    "RELIANCE":["reliance industries","reliance"],"TCS":["tata consultancy services","tcs"],"INFY":["infosys","infy"],"HDFCBANK":["hdfc bank","hdfcbank"],"ICICIBANK":["icici bank","icicibank"],"SBIN":["state bank of india","sbi"],"AXISBANK":["axis bank"],"KOTAKBANK":["kotak mahindra bank","kotak bank"],"INDUSINDBK":["indusind bank"],"BAJFINANCE":["bajaj finance"],"BAJAJFINSV":["bajaj finserv"],"LT":["larsen & toubro","larsen and toubro"],"BHARTIARTL":["bharti airtel","airtel"],"ITC":["itc limited","itc"],"HINDUNILVR":["hindustan unilever","hul"],"MARUTI":["maruti suzuki","maruti"],"M&M":["mahindra & mahindra","mahindra and mahindra"],"TATAMOTORS":["tata motors"],"SUNPHARMA":["sun pharma","sun pharmaceutical"],"DRREDDY":["dr reddy","dr. reddy"],"CIPLA":["cipla"],"DIVISLAB":["divi's laboratories","divis laboratories"],"APOLLOHOSP":["apollo hospitals","apollo hospital"],"WIPRO":["wipro"],"HCLTECH":["hcl technologies","hcltech"],"TECHM":["tech mahindra"],"LTIM":["ltimindtree","lti mindtree"],"TITAN":["titan company","titan"],"ASIANPAINT":["asian paints"],"ULTRACEMCO":["ultratech cement","ultratech"],"TATASTEEL":["tata steel"],"JSWSTEEL":["jsw steel"],"HINDALCO":["hindalco"],"COALINDIA":["coal india"],"ONGC":["ongc","oil and natural gas corporation"],"NTPC":["ntpc"],"POWERGRID":["power grid corporation","powergrid"],"ADANIENT":["adani enterprises"],"ADANIPORTS":["adani ports"],"GRASIM":["grasim industries","grasim"],"NESTLEIND":["nestle india"],"BRITANNIA":["britannia industries","britannia"],"EICHERMOT":["eicher motors"],"HEROMOTOCO":["hero motocorp","hero moto"]}


def _clamp(value, low=-10.0, high=10.0): return max(low,min(high,value))
def _headline_sentiment(title):
    text=title.lower(); return max(-2,min(2,sum(1 for w in BULLISH_WORDS if w in text)-sum(1 for w in BEARISH_WORDS if w in text)))
def _gift_weight():
    now=datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday()>=5:return 1.0,"WEEKEND/NEXT_SESSION"
    if time(9,15)<=now.time()<time(15,15):return 0.35,"REGULAR_NSE_HOURS"
    return 1.0,"PREOPEN_OR_OVERNIGHT"
def _clean_web_text(raw): return html.unescape(re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",raw)))

def _parse_gift_derivatives_watch(text):
    row=re.search(r"Index\s+Futures\s+NIFTY\s+(?P<expiry>\d{1,2}-[A-Za-z]{3}-\d{4})\s+-\s+-\s+(?P<ltp>[0-9,]+(?:\.[0-9]+)?)\s+(?P<change>[+-]?[0-9,]+(?:\.[0-9]+)?)\s+(?P<pct>[+-]?[0-9]+(?:\.[0-9]+)?)",text,re.I)
    if not row: raise ValueError("NIFTY futures quote not parseable on NSE IX")
    return {"ltp":float(row.group("ltp").replace(",","")),"change":float(row.group("change").replace(",","")),"pct":float(row.group("pct")),"expiry":row.group("expiry")}

def _parse_investing_gift(raw):
    # Investing pages usually expose quote values in visible text and/or JSON attributes.
    text=_clean_web_text(raw)
    ltp=None; pct=None; change=None
    for pat in [r'data-test="instrument-price-last"[^>]*>([0-9,]+(?:\.[0-9]+)?)',r'GIFT\s+NIFTY[^0-9]{0,120}([12][0-9],[0-9]{3}(?:\.[0-9]+)?)']:
        m=re.search(pat,raw if 'data-test' in pat else text,re.I)
        if m: ltp=float(m.group(1).replace(",","")); break
    m=re.search(r'data-test="instrument-price-change-percent"[^>]*>\s*\(?([+-]?[0-9]+(?:\.[0-9]+)?)%',raw,re.I)
    if not m: m=re.search(r'([+-]?[0-9]+(?:\.[0-9]+)?)%\s*(?:GIFT|NIFTY)',text,re.I)
    if m:pct=float(m.group(1))
    m=re.search(r'data-test="instrument-price-change"[^>]*>\s*([+-]?[0-9,]+(?:\.[0-9]+)?)',raw,re.I)
    if m:change=float(m.group(1).replace(",",""))
    if ltp is None or pct is None or not (5000<=ltp<=50000) or abs(pct)>10: raise ValueError("Fallback quote failed validation")
    return {"ltp":ltp,"change":change,"pct":pct,"expiry":None}

def _gift_result(parsed,source,url,warning):
    pct=parsed["pct"]; bias="BULLISH" if pct>=0.35 else "BEARISH" if pct<=-0.35 else "NEUTRAL"
    raw_score=_clamp(pct*4.0,-6.0,6.0); weight,regime=_gift_weight()
    return {"status":"AVAILABLE","source":source,"source_url":url,"ltp":parsed["ltp"],"change":parsed.get("change"),"change_pct":round(pct,2),"expiry":parsed.get("expiry"),"bias":bias,"raw_context_score":round(raw_score,1),"weight_applied":weight,"weight_regime":regime,"context_score":round(raw_score*weight,1),"fetched_at":datetime.now(timezone.utc).isoformat(),"warning":warning}

async def fetch_gift_nifty():
    headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36","Accept":"text/html,application/xhtml+xml","Accept-Language":"en-IN,en;q=0.9"}
    attempts=[]
    sources=[
        ("NSE IX Derivatives Watch","https://www.nseix.com/markets/derivatives-watch",_parse_gift_derivatives_watch,15),
        ("NSE IX Derivatives Watch","https://www1.nseix.com/markets/derivatives-watch",_parse_gift_derivatives_watch,15),
        ("Investing.com fallback","https://in.investing.com/indices/gift-nifty-50-c1-futures",_parse_investing_gift,12),
    ]
    for source,url,parser,timeout in sources:
        try:
            async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,headers=headers) as client:r=await client.get(url)
            r.raise_for_status(); parsed=parser(r.text)
            return _gift_result(parsed,source,url,"Public-web context only; fallback data is never treated as execution-grade and cannot create a trade.")
        except Exception as exc:
            attempts.append({"source":source,"error":str(exc) or exc.__class__.__name__})
    return {"status":"UNAVAILABLE","source":"NSE IX + fallback","bias":"UNKNOWN","context_score":0.0,"attempts":attempts,"error":"All GIFT NIFTY sources failed validation or were unreachable","fetched_at":datetime.now(timezone.utc).isoformat(),"warning":"No GIFT NIFTY adjustment applied."}

def _headline_is_relevant(title,symbol):
    text=title.lower(); aliases=[a.lower() for a in SYMBOL_ALIASES.get(symbol,[])]
    if any(a in text for a in aliases):return True
    ticker=symbol.lower(); return len(ticker)>=5 and re.search(rf"\b{re.escape(ticker)}\b",text) is not None

async def fetch_news_context(symbol):
    symbol=symbol.upper().strip(); aliases=SYMBOL_ALIASES.get(symbol,[symbol]); primary=aliases[0]
    query=quote_plus(f'"{primary}" stock NSE India when:1d'); url=f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(timeout=12,follow_redirects=True) as client:r=await client.get(url,headers={"User-Agent":"Mozilla/5.0 AlphaPilot/0.9"})
        r.raise_for_status(); root=ET.fromstring(r.text); headlines=[]; weighted=0.; weight_total=0.; now=datetime.now(timezone.utc); discarded=0
        for item in root.findall(".//item"):
            if len(headlines)>=8:break
            title=(item.findtext("title") or "").strip()
            if not _headline_is_relevant(title,symbol):discarded+=1;continue
            source=(item.findtext("source") or "").strip(); pub_raw=(item.findtext("pubDate") or "").strip(); published=None; age=None
            try:
                published=parsedate_to_datetime(pub_raw)
                if published.tzinfo is None:published=published.replace(tzinfo=timezone.utc)
                age=max(0.,(now-published.astimezone(timezone.utc)).total_seconds()/3600)
            except Exception:pass
            sentiment=_headline_sentiment(title); freshness=1. if age is None else max(.15,1.-min(age,24.)/28.); weighted+=sentiment*freshness; weight_total+=freshness
            headlines.append({"title":title,"source":source,"published_at":published.isoformat() if published else pub_raw or None,"age_hours":round(age,1) if age is not None else None,"sentiment":sentiment})
        raw=weighted/weight_total if weight_total else 0.; score=round(_clamp(raw*2.,-4.,4.),1); bias="BULLISH" if score>=1.5 else "BEARISH" if score<=-1.5 else "NEUTRAL"
        return {"status":"AVAILABLE" if headlines else "NO_RELEVANT_HEADLINES","source":"Google News RSS","symbol":symbol,"query_name":primary,"bias":bias,"context_score":score,"headline_count":len(headlines),"discarded_irrelevant":discarded,"headlines":headlines,"fetched_at":now.isoformat(),"warning":"Only stock-name-matched headlines are scored. News remains contextual and cannot create a trade by itself."}
    except Exception as exc:return {"status":"UNAVAILABLE","source":"Google News RSS","symbol":symbol,"bias":"UNKNOWN","context_score":0.,"headline_count":0,"headlines":[],"error":str(exc) or exc.__class__.__name__,"fetched_at":datetime.now(timezone.utc).isoformat()}

async def external_market_context(symbol):
    gift=await fetch_gift_nifty(); news=await fetch_news_context(symbol)
    return {"symbol":symbol.upper(),"gift_nifty":gift,"news":news,"combined_context_adjustment":round(_clamp(float(gift.get("context_score",0))+float(news.get("context_score",0)),-8.,8.),1),"rule":"Context can confirm or penalize an existing setup; it cannot promote NO_TRADE into SETUP."}
