import os, time, hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx
from app.engine import analyze_candles

class GrowwProvider:
    BASE_URL="https://api.groww.in"
    def __init__(self, settings):
        self.api_key="".join(os.getenv("GROWW_API_KEY","").split()); self.api_secret="".join(os.getenv("GROWW_API_SECRET","").split()); self.access_token="".join(os.getenv("GROWW_ACCESS_TOKEN","").split())
        if not self.access_token and (not self.api_key or not self.api_secret): raise RuntimeError("Set GROWW_ACCESS_TOKEN or both GROWW_API_KEY and GROWW_API_SECRET")
    async def _get_access_token(self):
        if self.access_token: return self.access_token
        ts=str(int(time.time())); checksum=hashlib.sha256((self.api_secret+ts).encode()).hexdigest()
        headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json","Accept":"application/json"}; payload={"key_type":"approval","checksum":checksum,"timestamp":ts}
        async with httpx.AsyncClient(timeout=20) as client: r=await client.post(f"{self.BASE_URL}/v1/token/api/access",headers=headers,json=payload)
        r.raise_for_status(); data=r.json(); token="".join(str(data.get("token","")).split())
        if not token: raise RuntimeError(f"Groww token generation failed: {data}")
        return token
    def _instrument(self,symbol):
        m={"NIFTY":("NSE","CASH","NIFTY","NSE-NIFTY"),"BANKNIFTY":("NSE","CASH","BANKNIFTY","NSE-BANKNIFTY"),"RELIANCE":("NSE","CASH","RELIANCE","NSE-RELIANCE"),"TCS":("NSE","CASH","TCS","NSE-TCS"),"INFY":("NSE","CASH","INFY","NSE-INFY"),"HDFCBANK":("NSE","CASH","HDFCBANK","NSE-HDFCBANK"),"ICICIBANK":("NSE","CASH","ICICIBANK","NSE-ICICIBANK"),"SBIN":("NSE","CASH","SBIN","NSE-SBIN")}
        if symbol not in m: raise ValueError(f"{symbol} is not mapped for historical scanning yet")
        return m[symbol]
    async def quote(self,symbol):
        token=await self._get_access_token(); ex,seg,tsym,_=self._instrument(symbol); headers={"Authorization":f"Bearer {token}","Accept":"application/json","X-API-VERSION":"1.0"}; params={"exchange":ex,"segment":seg,"trading_symbol":tsym}
        async with httpx.AsyncClient(timeout=20) as client: r=await client.get(f"{self.BASE_URL}/v1/live-data/quote",headers=headers,params=params)
        r.raise_for_status(); return {"provider":"GROWW","symbol":symbol,"exchange":ex,"segment":seg,"data":r.json()}
    async def candles(self,symbol,timeframe="15m"):
        token=await self._get_access_token(); ex,seg,_,gs=self._instrument(symbol); im={"5m":("5minute",7),"15m":("15minute",14),"1h":("1hour",60),"1d":("1day",180)}; ci,days=im.get(timeframe,("15minute",14)); now=datetime.now(ZoneInfo("Asia/Kolkata")); start=now-timedelta(days=days)
        headers={"Authorization":f"Bearer {token}","Accept":"application/json","X-API-VERSION":"1.0"}; params={"exchange":ex,"segment":seg,"groww_symbol":gs,"start_time":start.strftime("%Y-%m-%d %H:%M:%S"),"end_time":now.strftime("%Y-%m-%d %H:%M:%S"),"candle_interval":ci}
        async with httpx.AsyncClient(timeout=30) as client: r=await client.get(f"{self.BASE_URL}/v1/historical/candles",headers=headers,params=params)
        r.raise_for_status(); data=r.json(); payload=data.get("payload",data); return payload.get("candles",[])
    async def option_chain(self,symbol,expiry=None): return {"provider":"GROWW","symbol":symbol,"expiry":expiry,"status":"not_implemented_yet"}
    async def scan(self,symbols,timeframe,min_rr):
        results=[]
        for symbol in symbols:
            try: results.append(analyze_candles(symbol.upper(),await self.candles(symbol.upper(),timeframe),min_rr))
            except Exception as exc: results.append({"symbol":symbol.upper(),"status":"ERROR","error":str(exc)})
        setups=sorted([r for r in results if r.get("status")=="SETUP"],key=lambda x:x.get("alpha_score",0),reverse=True)
        return {"provider":"GROWW","timeframe":timeframe,"min_risk_reward":min_rr,"setups":setups,"others":[r for r in results if r.get("status")!="SETUP"],"warning":"Signals are research outputs, not guaranteed profits. Paper-test before real-money use."}
