import random, time

class MockProvider:
    base = {"NIFTY": 24850.5, "BANKNIFTY": 54200.0, "RELIANCE": 2945.8, "CRUDEOIL": 6580.0, "NATGAS": 285.4, "GOLD": 71250.0}
    async def quote(self, symbol):
        ltp = self.base.get(symbol, 1000.0)
        prev = ltp * 0.995
        return {"symbol": symbol, "ltp": ltp, "prevClose": prev, "change": round(ltp-prev,2), "changePct": round((ltp-prev)/prev*100,2), "timestamp": int(time.time()*1000), "status":"MOCK"}
    async def option_chain(self, symbol, expiry=None):
        spot = self.base.get(symbol, 1000.0)
        return {"symbol":symbol,"expiry":expiry,"spot":spot,"rows":[],"timestamp":int(time.time()*1000),"status":"MOCK"}
    async def scan(self, symbols, timeframe, min_rr):
        results=[]
        for s in symbols:
            q=await self.quote(s)
            direction=random.choice(["BULLISH","BEARISH","NEUTRAL"])
            confidence=random.randint(45,88)
            if direction != "NEUTRAL" and confidence >= 60:
                entry=q["ltp"]
                stop=round(entry*(0.99 if direction=="BULLISH" else 1.01),2)
                target=round(entry*(1.02 if direction=="BULLISH" else 0.98),2)
                results.append({"symbol":s,"direction":direction,"confidence":confidence,"entry":entry,"stopLoss":stop,"target":target,"riskReward":2.0,"dataStatus":"MOCK"})
        return {"timeframe":timeframe,"minRiskReward":min_rr,"results":results,"status":"MOCK"}
