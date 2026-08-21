from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.external_context import external_market_context
from .groww import GrowwProvider


class DynamicGrowwProvider(GrowwProvider):
    def _instrument(self, symbol):
        symbol=symbol.upper().strip()
        try:return super()._instrument(symbol)
        except ValueError:
            if not symbol or not symbol.replace("&","").replace("-","").isalnum():raise ValueError(f"Invalid NSE symbol: {symbol!r}")
            return ("NSE","CASH",symbol,f"NSE-{symbol}")

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

    async def fno_confirm(self,symbol,timeframes,min_rr,expiry=None,include_market=True,take_snapshot=True,manual_gift=None):
        result=await super().fno_confirm(symbol,timeframes,min_rr,expiry=expiry,include_market=include_market,take_snapshot=take_snapshot)
        external=await external_market_context(symbol,manual_gift=manual_gift);self._apply_external_context(result,external)
        session=self._market_session();result["market_session"]=session;result["execution_ready"]=False;result["execution_blockers"]=[]
        if result.get("status")=="SETUP":
            chain=await self.option_chain(symbol,result.get("expiry"));option=self._recommended_option(symbol,chain["expiry"],chain["data"],result.get("technical",{}));result["recommended_option"]=option
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
