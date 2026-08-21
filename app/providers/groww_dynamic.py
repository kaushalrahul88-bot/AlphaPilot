import asyncio
import hashlib
import logging
import time as time_module

import httpx
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.external_context import external_market_context
from .groww import GrowwProvider

logger = logging.getLogger("alphapilot.groww")


class DynamicGrowwProvider(GrowwProvider):
    _shared_access_token = None
    _shared_token_generated_at = 0.0
    _shared_auth_lock = None
    _shared_token_ttl_seconds = 12 * 60 * 60

    @classmethod
    def _auth_lock(cls):
        if cls._shared_auth_lock is None:
            cls._shared_auth_lock = asyncio.Lock()
        return cls._shared_auth_lock

    async def _get_access_token(self):
        now = time_module.time()
        if self.__class__._shared_access_token and now - self.__class__._shared_token_generated_at < self.__class__._shared_token_ttl_seconds:
            return self.__class__._shared_access_token
        if self._cached_token:
            self.__class__._shared_access_token = self._cached_token
            self.__class__._shared_token_generated_at = now
            return self._cached_token
        async with self.__class__._auth_lock():
            now = time_module.time()
            if self.__class__._shared_access_token and now - self.__class__._shared_token_generated_at < self.__class__._shared_token_ttl_seconds:
                return self.__class__._shared_access_token
            if self.api_key and self.api_secret:
                ts = str(int(now))
                checksum = hashlib.sha256((self.api_secret + ts).encode()).hexdigest()
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"}
                payload = {"key_type": "approval", "checksum": checksum, "timestamp": ts}
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.post(f"{self.BASE_URL}/v1/token/api/access", headers=headers, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    token = "".join(str(data.get("token", "")).split())
                    if token:
                        self._cached_token = token
                        self.__class__._shared_access_token = token
                        self.__class__._shared_token_generated_at = now
                        logger.info("GROWW_AUTH token generated once and cached process-wide")
                        return token
                detail = response.text[:500]
                if self.access_token:
                    logger.warning("GROWW_AUTH dynamic token failed status=%s; using configured access token", response.status_code)
                    return self.access_token
                raise RuntimeError(f"Groww daily authentication failed ({response.status_code}). Approve the API key for today in Groww Trading APIs. {detail}")
            if self.access_token:
                return self.access_token
            raise RuntimeError("No Groww authentication credentials are configured")

    def _instrument(self, symbol):
        symbol=symbol.upper().strip()
        try:return super()._instrument(symbol)
        except ValueError:
            if not symbol or not symbol.replace("&","").replace("-","").isalnum():raise ValueError(f"Invalid NSE symbol: {symbol!r}")
            return ("NSE","CASH",symbol,f"NSE-{symbol}")

    async def candles(self, symbol, timeframe="15m"):
        try:
            candles = await super().candles(symbol, timeframe)
            count = len(candles) if isinstance(candles, list) else -1
            sample = candles[-1] if isinstance(candles, list) and candles else None
            logger.warning("GROWW_CANDLES symbol=%s timeframe=%s type=%s count=%s sample=%r", symbol, timeframe, type(candles).__name__, count, sample)
            if not isinstance(candles, list): logger.error("GROWW_CANDLES_INVALID symbol=%s timeframe=%s value=%r", symbol, timeframe, candles)
            elif not candles: logger.error("GROWW_CANDLES_EMPTY symbol=%s timeframe=%s", symbol, timeframe)
            return candles
        except Exception as exc:
            logger.exception("GROWW_CANDLES_ERROR symbol=%s timeframe=%s error=%s", symbol, timeframe, exc)
            raise

    def _market_session(self):
        now=datetime.now(ZoneInfo("Asia/Kolkata")); current=now.time(); weekday=now.weekday()<5
        if not weekday or current<time(9,15) or current>time(15,40):phase="CLOSED";allowed=False;desc="NSE equity derivatives session is closed.";window="MARKET CLOSED · F&O closes 15:40 IST"
        elif current<time(15,15):phase="CONTINUOUS";allowed=True;desc="Normal cash + F&O continuous market session.";window="CONTINUOUS MARKET · 09:15-15:15 IST"
        elif current<=time(15,35):phase="CLOSING_AUCTION";allowed=False;desc="Underlying F&O stocks are in NSE Closing Auction Session.";window="CLOSING AUCTION · 15:15-15:35 IST"
        else:phase="FNO_ONLY";allowed=False;desc="Cash auction ended while F&O remains tradable until 15:40; fresh entries blocked.";window="F&O-ONLY WINDOW · 15:35-15:40 IST"
        return {"timezone":"Asia/Kolkata","checked_at":now.isoformat(),"is_open":allowed,"status":phase,"phase":phase,"execution_allowed":allowed,"description":desc,"regular_hours":window,"continuous_cash_hours":"09:15-15:15 IST, Monday-Friday","closing_auction_window":"15:15-15:35 IST","fno_only_window":"15:35-15:40 IST","derivatives_close":"15:40 IST"}

    def _recommended_option(self,symbol,expiry,raw_chain,technical):
        if technical.get("status")!="SETUP" or technical.get("direction") not in ("LONG","SHORT"):return None
        spot,rows=self._normalize_option_chain(raw_chain)
        if not spot or not rows:return None
        atm=min(rows,key=lambda r:abs(r["strike"]-spot)); direction=technical["direction"]; option_type="CE" if direction=="LONG" else "PE"; prefix="ce" if option_type=="CE" else "pe"
        return {"underlying":symbol,"expiry":expiry,"direction":direction,"option_type":option_type,"strike":atm["strike"],"contract_label":f"{symbol} {expiry} {int(atm['strike'])} {option_type}","premium":atm.get(f"{prefix}_ltp"),"iv":atm.get(f"{prefix}_iv"),"open_interest":int(atm.get(f"{prefix}_oi") or 0),"volume":int(atm.get(f"{prefix}_volume") or 0),"underlying_ltp":spot,"selection_method":"ATM contract aligned with confirmed technical direction","warning":"Research contract suggestion only. Verify live spread, lot size, liquidity and slippage before execution."}

    def _apply_external_context(self,result,external):
        technical=result.get("technical",{}); direction=technical.get("direction")
        result["external_context"]=external
        if direction not in ("LONG","SHORT"):result["external_context_adjustment"]=0.;return
        gift=external.get("gift_nifty",{}); news=external.get("news",{}); signed=float(gift.get("context_score") or 0)+float(news.get("context_score") or 0)
        adjustment=max(-8.,min(8.,signed)); original=float(result.get("overall_alpha_score",50))
        adjusted=original+adjustment if direction=="LONG" else original-adjustment
        result["overall_alpha_score_before_external"]=round(original,1);result["overall_alpha_score"]=round(max(0,min(100,adjusted)),1);result["external_context_adjustment"]=round(adjustment if direction=="LONG" else -adjustment,1)
        reasons=result.setdefault("score_adjustments",[])
        if gift.get("status")=="AVAILABLE" and abs(float(gift.get("context_score") or 0))>=.5:reasons.append(f"GIFT NIFTY {gift.get('bias','UNKNOWN')} ({'MANUAL' if gift.get('manual') else 'AUTO'}): {gift.get('change_pct')}%")
        if news.get("status") in ("AVAILABLE","NO_RELEVANT_HEADLINES") and abs(float(news.get("context_score") or 0))>=.5:reasons.append(f"Recent news context {news.get('bias','NEUTRAL')}: {float(news.get('context_score') or 0):+.1f}")

    @staticmethod
    def _trade_plan_complete(technical):
        if not isinstance(technical, dict): return False
        if technical.get("trade_plan_complete") is False: return False
        for key in ("entry", "stop_loss", "target1", "risk_reward"):
            value = technical.get(key)
            if not isinstance(value, (int, float)) or value <= 0: return False
        return True

    async def fno_confirm(self,symbol,timeframes,min_rr,expiry=None,include_market=True,take_snapshot=True,manual_gift=None):
        result=await super().fno_confirm(symbol,timeframes,min_rr,expiry=expiry,include_market=include_market,take_snapshot=take_snapshot)
        external=await external_market_context(symbol,manual_gift=manual_gift);self._apply_external_context(result,external)
        session=self._market_session();result["market_session"]=session;result["execution_ready"]=False;result["execution_blockers"]=[]
        if result.get("status")=="SETUP":
            technical=result.get("technical",{})
            chain=await self.option_chain(symbol,result.get("expiry"));option=self._recommended_option(symbol,chain["expiry"],chain["data"],technical);result["recommended_option"]=option
            if not self._trade_plan_complete(technical): result["execution_blockers"].append("DATA_INCOMPLETE: underlying entry/stop/target/R:R trade plan is missing or invalid.")
            if not session["execution_allowed"]:
                if session["phase"]=="CLOSING_AUCTION":result["execution_blockers"].append("NSE Closing Auction Session is active (15:15-15:35 IST); fresh BEST TRADE entries are blocked.")
                elif session["phase"]=="FNO_ONLY":result["execution_blockers"].append("F&O-only closing window is active (15:35-15:40 IST); fresh BEST TRADE entries are blocked.")
                else:result["execution_blockers"].append("NSE equity derivatives market is closed; underlying and option premiums may be stale.")
            if not option or not isinstance(option.get("premium"),(int,float)) or option.get("premium",0)<=0:result["execution_blockers"].append("No valid positive option premium is available for the recommended contract.")
            if not option or option.get("open_interest",0)<=0:result["execution_blockers"].append("Recommended contract has no reported open interest.")
            result["execution_ready"]=not result["execution_blockers"]
            if not result["execution_ready"]:result["status"]="NO_TRADE"
        else:result["recommended_option"]=None
        return result
