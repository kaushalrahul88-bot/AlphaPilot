import os
import time
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx
from app.engine import analyze_candles

class GrowwProvider:
    BASE_URL="https://api.groww.in"

    def __init__(self,settings):
        self.api_key="".join(os.getenv("GROWW_API_KEY","").split())
        self.api_secret="".join(os.getenv("GROWW_API_SECRET","").split())
        self.access_token="".join(os.getenv("GROWW_ACCESS_TOKEN","").split())
        self._cached_token=None
        if not self.access_token and (not self.api_key or not self.api_secret):
            raise RuntimeError("Set GROWW_ACCESS_TOKEN or both GROWW_API_KEY and GROWW_API_SECRET")

    async def _get_access_token(self):
        if self.access_token:return self.access_token
        if self._cached_token:return self._cached_token
        ts=str(int(time.time()))
        checksum=hashlib.sha256((self.api_secret+ts).encode()).hexdigest()
        headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json","Accept":"application/json"}
        payload={"key_type":"approval","checksum":checksum,"timestamp":ts}
        async with httpx.AsyncClient(timeout=20) as client:
            r=await client.post(f"{self.BASE_URL}/v1/token/api/access",headers=headers,json=payload)
        r.raise_for_status()
        data=r.json(); token="".join(str(data.get("token","")).split())
        if not token:raise RuntimeError(f"Groww token generation failed: {data}")
        self._cached_token=token
        return token

    def _instrument(self,symbol):
        m={
          "NIFTY":("NSE","CASH","NIFTY","NSE-NIFTY"),
          "BANKNIFTY":("NSE","CASH","BANKNIFTY","NSE-BANKNIFTY"),
          "RELIANCE":("NSE","CASH","RELIANCE","NSE-RELIANCE"),
          "TCS":("NSE","CASH","TCS","NSE-TCS"),"INFY":("NSE","CASH","INFY","NSE-INFY"),
          "HDFCBANK":("NSE","CASH","HDFCBANK","NSE-HDFCBANK"),
          "ICICIBANK":("NSE","CASH","ICICIBANK","NSE-ICICIBANK"),"SBIN":("NSE","CASH","SBIN","NSE-SBIN")}
        if symbol not in m:raise ValueError(f"{symbol} is not mapped yet")
        return m[symbol]

    async def _headers(self):
        return {"Authorization":f"Bearer {await self._get_access_token()}","Accept":"application/json","X-API-VERSION":"1.0"}

    async def quote(self,symbol):
        ex,seg,ts,_=self._instrument(symbol)
        async with httpx.AsyncClient(timeout=20) as client:
            r=await client.get(f"{self.BASE_URL}/v1/live-data/quote",headers=await self._headers(),
                               params={"exchange":ex,"segment":seg,"trading_symbol":ts})
        r.raise_for_status()
        return {"provider":"GROWW","symbol":symbol,"exchange":ex,"segment":seg,"data":r.json()}

    async def candles(self,symbol,timeframe="15m"):
        ex,seg,_,gs=self._instrument(symbol)
        im={"5m":("5minute",7),"15m":("15minute",14),"1h":("1hour",60),"1d":("1day",240)}
        ci,days=im.get(timeframe,("15minute",14))
        now=datetime.now(ZoneInfo("Asia/Kolkata")); start=now-timedelta(days=days)
        params={"exchange":ex,"segment":seg,"groww_symbol":gs,
                "start_time":start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time":now.strftime("%Y-%m-%d %H:%M:%S"),"candle_interval":ci}
        async with httpx.AsyncClient(timeout=30) as client:
            r=await client.get(f"{self.BASE_URL}/v1/historical/candles",headers=await self._headers(),params=params)
        r.raise_for_status(); data=r.json(); payload=data.get("payload",data)
        return payload.get("candles",[])

    async def expiries(self,symbol):
        now=datetime.now(ZoneInfo("Asia/Kolkata"))
        found=[]
        # current + next month to handle month-end
        for offset in (0,32):
            d=now+timedelta(days=offset)
            params={"exchange":"NSE","underlying_symbol":symbol,"year":d.year,"month":d.month}
            async with httpx.AsyncClient(timeout=20) as client:
                r=await client.get(f"{self.BASE_URL}/v1/historical/expiries",headers=await self._headers(),params=params)
            if r.status_code==200:
                p=r.json().get("payload",{})
                found.extend(p.get("expiries",[]))
        today=now.date().isoformat()
        return sorted(set(x for x in found if x>=today))

    async def option_chain(self,symbol,expiry=None):
        if not expiry:
            exps=await self.expiries(symbol)
            if not exps:raise RuntimeError(f"No future expiry found for {symbol}")
            expiry=exps[0]
        url=f"{self.BASE_URL}/v1/option-chain/exchange/NSE/underlying/{symbol}"
        async with httpx.AsyncClient(timeout=30) as client:
            r=await client.get(url,headers=await self._headers(),params={"expiry_date":expiry})
        r.raise_for_status()
        return {"provider":"GROWW","symbol":symbol,"expiry":expiry,"data":r.json()}

    def _fno_analytics(self,raw):
        payload=raw.get("payload",raw)
        spot=float(payload.get("underlying_ltp") or 0)
        strikes=payload.get("strikes",{})
        rows=[]
        total_ce_oi=total_pe_oi=total_ce_vol=total_pe_vol=0
        for sk,v in strikes.items():
            try: strike=float(sk)
            except: continue
            ce=v.get("CE") or {}; pe=v.get("PE") or {}
            ceoi=float(ce.get("open_interest") or 0); peoi=float(pe.get("open_interest") or 0)
            cev=float(ce.get("volume") or 0); pev=float(pe.get("volume") or 0)
            total_ce_oi+=ceoi; total_pe_oi+=peoi; total_ce_vol+=cev; total_pe_vol+=pev
            rows.append({"strike":strike,"ce_oi":ceoi,"pe_oi":peoi,
                         "ce_ltp":ce.get("ltp"),"pe_ltp":pe.get("ltp"),
                         "ce_iv":(ce.get("greeks") or {}).get("iv"),
                         "pe_iv":(pe.get("greeks") or {}).get("iv")})
        pcr=total_pe_oi/total_ce_oi if total_ce_oi else None
        call_wall=max(rows,key=lambda x:x["ce_oi"],default=None)
        put_wall=max(rows,key=lambda x:x["pe_oi"],default=None)
        atm=min(rows,key=lambda x:abs(x["strike"]-spot),default=None) if spot and rows else None
        bias_score=50
        reasons=[]; warnings=[]
        if pcr is not None:
            if 0.9<=pcr<=1.3: bias_score+=8; reasons.append("PCR supportive/neutral-bullish")
            elif pcr<0.7: bias_score-=10; reasons.append("Low PCR / call-heavy positioning")
            elif pcr>1.5: warnings.append("Very high PCR; possible crowded put positioning")
        if put_wall and call_wall and spot:
            if put_wall["strike"]<spot<call_wall["strike"]:
                reasons.append("Spot trading between major put support and call resistance")
            if abs(call_wall["strike"]-spot)<abs(put_wall["strike"]-spot):
                warnings.append("Major call OI resistance is relatively close")
        atm_iv=None
        if atm:
            ivs=[x for x in (atm.get("ce_iv"),atm.get("pe_iv")) if isinstance(x,(int,float))]
            atm_iv=sum(ivs)/len(ivs) if ivs else None
        return {
          "underlying_ltp":spot,"pcr_oi":round(pcr,3) if pcr is not None else None,
          "total_call_oi":int(total_ce_oi),"total_put_oi":int(total_pe_oi),
          "total_call_volume":int(total_ce_vol),"total_put_volume":int(total_pe_vol),
          "call_resistance_strike":call_wall["strike"] if call_wall else None,
          "put_support_strike":put_wall["strike"] if put_wall else None,
          "atm_strike":atm["strike"] if atm else None,
          "atm_iv":round(atm_iv,2) if atm_iv is not None else None,
          "fno_score":max(0,min(100,bias_score)),"reasons":reasons,"warnings":warnings
        }

    async def scan(self,symbols,timeframe,min_rr):
        results=[]
        for s in symbols:
            try: results.append(analyze_candles(s.upper(),await self.candles(s.upper(),timeframe),min_rr))
            except Exception as e:results.append({"symbol":s.upper(),"status":"ERROR","error":str(e)})
        setups=sorted([x for x in results if x.get("status")=="SETUP"],key=lambda x:x.get("alpha_score",0),reverse=True)
        return {"provider":"GROWW","timeframe":timeframe,"min_risk_reward":min_rr,"setups":setups,
                "others":[x for x in results if x.get("status")!="SETUP"],
                "warning":"Signals are research outputs, not guaranteed profits. Paper-test before real-money use."}

    async def multi_timeframe_scan(self,symbols,timeframes,min_rr):
        results=[]
        weights={"5m":.20,"15m":.35,"1h":.30,"1d":.15}
        for s in symbols:
            tf={}
            for t in timeframes:
                try:tf[t]=analyze_candles(s.upper(),await self.candles(s.upper(),t),min_rr)
                except Exception as e:tf[t]={"symbol":s.upper(),"status":"ERROR","error":str(e)}
            valid=[v for v in tf.values() if v.get("status")!="ERROR"]
            if not valid:
                results.append({"symbol":s.upper(),"status":"ERROR","timeframes":tf});continue
            ws=sum(tf[t].get("alpha_score",50)*weights.get(t,.25) for t in tf if tf[t].get("status")!="ERROR")
            wu=sum(weights.get(t,.25) for t in tf if tf[t].get("status")!="ERROR")
            score=ws/wu if wu else 50
            lv=sum(1 for v in valid if v.get("signal") in ("LONG","STRONG_LONG","WATCH_LONG") and v.get("alpha_score",0)>=58)
            sv=sum(1 for v in valid if v.get("signal") in ("SHORT","STRONG_SHORT","WATCH_SHORT") and v.get("alpha_score",100)<=42)
            sl=sum(1 for v in valid if v.get("status")=="SETUP" and v.get("direction")=="LONG")
            ss=sum(1 for v in valid if v.get("status")=="SETUP" and v.get("direction")=="SHORT")
            htb=any(tf.get(t,{}).get("alpha_score",50)>=55 for t in ("1h","1d") if t in tf)
            hts=any(tf.get(t,{}).get("alpha_score",50)<=45 for t in ("1h","1d") if t in tf)
            n=len(valid)
            if score>=68 and lv>=max(2,n-1) and sl>=1 and not hts:status,direction="SETUP","LONG"
            elif score<=32 and sv>=max(2,n-1) and ss>=1 and not htb:status,direction="SETUP","SHORT"
            else:status,direction="NO_TRADE",None
            signal=("STRONG_LONG" if status=="SETUP" and direction=="LONG" and score>=80 and lv==n else
                    "LONG" if status=="SETUP" and direction=="LONG" else
                    "STRONG_SHORT" if status=="SETUP" and direction=="SHORT" and score<=20 and sv==n else
                    "SHORT" if status=="SETUP" and direction=="SHORT" else
                    "WATCH_LONG" if score>=58 else "WATCH_SHORT" if score<=42 else "NO_TRADE")
            item={"symbol":s.upper(),"status":status,"signal":signal,"multi_timeframe_score":round(score,1),
                  "timeframe_votes":{"long":lv,"short":sv,"valid":n},
                  "higher_timeframe":{"bullish":htb,"bearish":hts},"timeframes":tf}
            if status=="SETUP":
                ex=tf.get("15m",valid[0]);item.update({"direction":direction,"execution_timeframe":"15m" if "15m" in tf else timeframes[0],
                "entry":ex.get("entry"),"stop_loss":ex.get("stop_loss"),"target1":ex.get("target1"),"target2":ex.get("target2"),
                "risk_reward":ex.get("risk_reward")})
            else:item["reason"]="Multi-timeframe alignment threshold not met"
            results.append(item)
        return {"provider":"GROWW","mode":"MULTI_TIMEFRAME","timeframes":timeframes,"min_risk_reward":min_rr,
                "setups":[x for x in results if x.get("status")=="SETUP"],
                "others":[x for x in results if x.get("status")!="SETUP"]}

    async def fno_confirm(self,symbol,timeframes,min_rr,expiry=None):
        mtf=await self.multi_timeframe_scan([symbol],timeframes,min_rr)
        tech=(mtf.get("setups") or mtf.get("others") or [{}])[0]
        oc=await self.option_chain(symbol,expiry)
        fno=self._fno_analytics(oc["data"])
        tech_score=float(tech.get("multi_timeframe_score",50))
        fno_score=float(fno.get("fno_score",50))
        overall=round(tech_score*.75+fno_score*.25,1)
        # F&O confirms context; it cannot turn a technical NO_TRADE into an executable setup.
        status=tech.get("status","NO_TRADE")
        signal=tech.get("signal","NO_TRADE")
        return {"provider":"GROWW","mode":"MTF_FNO_CONFIRMATION","symbol":symbol,
                "expiry":oc["expiry"],"overall_alpha_score":overall,
                "technical_score":tech_score,"fno_score":fno_score,
                "status":status,"signal":signal,"technical":tech,"fno":fno,
                "warning":"OI/PCR/IV are context signals, not standalone trade triggers. This version does not infer OI buildup from a single snapshot."}
