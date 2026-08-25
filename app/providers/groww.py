import os
import time
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.engine import analyze_candles


class GrowwProvider:
    BASE_URL = "https://api.groww.in"

    # Intraday process-local snapshot cache.
    # This intentionally avoids pretending Render's free filesystem is durable.
    _option_snapshots = {}

    # Cash symbols used by AlphaPilot research/scanning. Keeping this allow-list
    # explicit prevents arbitrary user input from becoming an upstream request,
    # while allowing Market Brain to build its full breadth context.
    NSE_CASH_SYMBOLS = {
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
        "AXISBANK", "KOTAKBANK", "INDUSINDBK", "BAJFINANCE", "BAJAJFINSV",
        "LT", "BHARTIARTL", "ITC", "HINDUNILVR", "MARUTI", "M&M",
        "TATAMOTORS", "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB",
        "APOLLOHOSP", "WIPRO", "HCLTECH", "TECHM", "LTIM", "TITAN",
        "ASIANPAINT", "ULTRACEMCO", "TATASTEEL", "JSWSTEEL", "HINDALCO",
        "COALINDIA", "ONGC", "NTPC", "POWERGRID", "ADANIENT",
        "ADANIPORTS", "GRASIM", "NESTLEIND", "BRITANNIA", "EICHERMOT",
        "HEROMOTOCO",
    }

    def __init__(self, settings):
        self.api_key = "".join(os.getenv("GROWW_API_KEY", "").split())
        self.api_secret = "".join(os.getenv("GROWW_API_SECRET", "").split())
        self.access_token = "".join(os.getenv("GROWW_ACCESS_TOKEN", "").split())
        self._cached_token = None

        if not self.access_token and (not self.api_key or not self.api_secret):
            raise RuntimeError(
                "Set GROWW_ACCESS_TOKEN or both GROWW_API_KEY and GROWW_API_SECRET"
            )

    async def _get_access_token(self):
        if self.access_token:
            return self.access_token
        if self._cached_token:
            return self._cached_token

        ts = str(int(time.time()))
        checksum = hashlib.sha256(
            (self.api_secret + ts).encode()
        ).hexdigest()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "key_type": "approval",
            "checksum": checksum,
            "timestamp": ts,
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.BASE_URL}/v1/token/api/access",
                headers=headers,
                json=payload,
            )

        response.raise_for_status()
        data = response.json()
        token = "".join(str(data.get("token", "")).split())

        if not token:
            raise RuntimeError(
                f"Groww token generation failed: {data}"
            )

        self._cached_token = token
        return token

    async def _headers(self):
        return {
            "Authorization": f"Bearer {await self._get_access_token()}",
            "Accept": "application/json",
            "X-API-VERSION": "1.0",
        }

    def _instrument(self, symbol):
        mapping = {
            "NIFTY": ("NSE", "CASH", "NIFTY", "NSE-NIFTY"),
            "BANKNIFTY": ("NSE", "CASH", "BANKNIFTY", "NSE-BANKNIFTY"),
            "RELIANCE": ("NSE", "CASH", "RELIANCE", "NSE-RELIANCE"),
            "TCS": ("NSE", "CASH", "TCS", "NSE-TCS"),
            "INFY": ("NSE", "CASH", "INFY", "NSE-INFY"),
            "HDFCBANK": ("NSE", "CASH", "HDFCBANK", "NSE-HDFCBANK"),
            "ICICIBANK": ("NSE", "CASH", "ICICIBANK", "NSE-ICICIBANK"),
            "SBIN": ("NSE", "CASH", "SBIN", "NSE-SBIN"),
        }
        if symbol in mapping:
            return mapping[symbol]
        if symbol in self.NSE_CASH_SYMBOLS:
            return ("NSE", "CASH", symbol, f"NSE-{symbol}")
        raise ValueError(f"{symbol} is not mapped yet")

    async def quote(self, symbol):
        exchange, segment, trading_symbol, _ = self._instrument(symbol)

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.BASE_URL}/v1/live-data/quote",
                headers=await self._headers(),
                params={
                    "exchange": exchange,
                    "segment": segment,
                    "trading_symbol": trading_symbol,
                },
            )

        response.raise_for_status()
        return {
            "provider": "GROWW",
            "symbol": symbol,
            "exchange": exchange,
            "segment": segment,
            "data": response.json(),
        }

    async def candles(self, symbol, timeframe="15m"):
        exchange, segment, _, groww_symbol = self._instrument(symbol)

        interval_map = {
            "5m": ("5minute", 7),
            "15m": ("15minute", 14),
            "1h": ("1hour", 60),
            "1d": ("1day", 240),
        }
        candle_interval, days = interval_map.get(
            timeframe,
            ("15minute", 14),
        )

        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        start = now - timedelta(days=days)

        params = {
            "exchange": exchange,
            "segment": segment,
            "groww_symbol": groww_symbol,
            "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "candle_interval": candle_interval,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.BASE_URL}/v1/historical/candles",
                headers=await self._headers(),
                params=params,
            )

        response.raise_for_status()
        data = response.json()
        payload = data.get("payload", data)
        return payload.get("candles", [])

    async def expiries(self, symbol):
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        found = []

        for offset in (0, 32):
            d = now + timedelta(days=offset)
            params = {
                "exchange": "NSE",
                "underlying_symbol": symbol,
                "year": d.year,
                "month": d.month,
            }

            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"{self.BASE_URL}/v1/historical/expiries",
                    headers=await self._headers(),
                    params=params,
                )

            if response.status_code == 200:
                payload = response.json().get("payload", {})
                found.extend(payload.get("expiries", []))

        today = now.date().isoformat()
        return sorted(set(x for x in found if x >= today))

    async def option_chain(self, symbol, expiry=None):
        if not expiry:
            expiries = await self.expiries(symbol)
            if not expiries:
                raise RuntimeError(
                    f"No future expiry found for {symbol}"
                )
            expiry = expiries[0]

        url = (
            f"{self.BASE_URL}/v1/option-chain/exchange/NSE/"
            f"underlying/{symbol}"
        )

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                url,
                headers=await self._headers(),
                params={"expiry_date": expiry},
            )

        response.raise_for_status()
        return {
            "provider": "GROWW",
            "symbol": symbol,
            "expiry": expiry,
            "data": response.json(),
        }

    def _normalize_option_chain(self, raw):
        payload = raw.get("payload", raw)
        spot = float(payload.get("underlying_ltp") or 0)
        strikes = payload.get("strikes", {})
        rows = []

        for strike_key, value in strikes.items():
            try:
                strike = float(strike_key)
            except (TypeError, ValueError):
                continue

            ce = value.get("CE") or {}
            pe = value.get("PE") or {}

            rows.append({
                "strike": strike,
                "ce_oi": float(ce.get("open_interest") or 0),
                "pe_oi": float(pe.get("open_interest") or 0),
                "ce_volume": float(ce.get("volume") or 0),
                "pe_volume": float(pe.get("volume") or 0),
                "ce_ltp": ce.get("ltp"),
                "pe_ltp": pe.get("ltp"),
                "ce_iv": (ce.get("greeks") or {}).get("iv"),
                "pe_iv": (pe.get("greeks") or {}).get("iv"),
            })

        return spot, rows

    def _fno_analytics(self, raw):
        spot, rows = self._normalize_option_chain(raw)

        total_ce_oi = sum(x["ce_oi"] for x in rows)
        total_pe_oi = sum(x["pe_oi"] for x in rows)
        total_ce_vol = sum(x["ce_volume"] for x in rows)
        total_pe_vol = sum(x["pe_volume"] for x in rows)

        pcr = (
            total_pe_oi / total_ce_oi
            if total_ce_oi
            else None
        )

        call_wall = max(
            rows,
            key=lambda x: x["ce_oi"],
            default=None,
        )
        put_wall = max(
            rows,
            key=lambda x: x["pe_oi"],
            default=None,
        )

        atm = (
            min(
                rows,
                key=lambda x: abs(x["strike"] - spot),
            )
            if spot and rows
            else None
        )

        atm_iv = None
        if atm:
            ivs = [
                x
                for x in (atm.get("ce_iv"), atm.get("pe_iv"))
                if isinstance(x, (int, float))
            ]
            atm_iv = (
                sum(ivs) / len(ivs)
                if ivs
                else None
            )

        score = 50
        reasons = []
        warnings = []

        if pcr is not None:
            if 0.9 <= pcr <= 1.3:
                score += 8
                reasons.append(
                    "PCR supportive / balanced"
                )
            elif pcr < 0.7:
                score -= 10
                reasons.append(
                    "Low PCR / call-heavy positioning"
                )
            elif pcr > 1.5:
                warnings.append(
                    "Very high PCR; possible crowded put positioning"
                )

        if put_wall and call_wall and spot:
            if (
                put_wall["strike"]
                < spot
                < call_wall["strike"]
            ):
                reasons.append(
                    "Spot between major put support and call resistance"
                )

            call_distance = (
                call_wall["strike"] - spot
            )
            put_distance = (
                spot - put_wall["strike"]
            )

            if (
                call_distance >= 0
                and call_distance < put_distance
            ):
                score -= 4
                warnings.append(
                    "Major call OI resistance is relatively close"
                )

        return {
            "underlying_ltp": spot,
            "pcr_oi": round(pcr, 3)
            if pcr is not None
            else None,
            "total_call_oi": int(total_ce_oi),
            "total_put_oi": int(total_pe_oi),
            "total_call_volume": int(total_ce_vol),
            "total_put_volume": int(total_pe_vol),
            "call_resistance_strike": (
                call_wall["strike"]
                if call_wall
                else None
            ),
            "put_support_strike": (
                put_wall["strike"]
                if put_wall
                else None
            ),
            "atm_strike": (
                atm["strike"]
                if atm
                else None
            ),
            "atm_iv": (
                round(atm_iv, 2)
                if atm_iv is not None
                else None
            ),
            "fno_score": max(0, min(100, score)),
            "reasons": reasons,
            "warnings": warnings,
        }

    def _snapshot_key(self, symbol, expiry):
        return f"{symbol}:{expiry}"

    def _snapshot_payload(self, symbol, expiry, raw):
        spot, rows = self._normalize_option_chain(raw)
        analytics = self._fno_analytics(raw)

        return {
            "captured_at": datetime.now(
                ZoneInfo("Asia/Kolkata")
            ).isoformat(),
            "symbol": symbol,
            "expiry": expiry,
            "spot": spot,
            "pcr_oi": analytics["pcr_oi"],
            "total_call_oi": analytics["total_call_oi"],
            "total_put_oi": analytics["total_put_oi"],
            "atm_iv": analytics["atm_iv"],
            "call_resistance_strike": analytics[
                "call_resistance_strike"
            ],
            "put_support_strike": analytics[
                "put_support_strike"
            ],
            "rows": rows,
        }

    def _snapshot_delta(self, previous, current):
        if not previous:
            return {
                "status": "FIRST_SNAPSHOT",
                "message": (
                    "Take another snapshot later to measure OI/PCR/IV change."
                ),
            }

        def delta(new, old):
            if (
                new is None
                or old is None
            ):
                return None
            return round(new - old, 3)

        price_change = delta(
            current.get("spot"),
            previous.get("spot"),
        )
        call_oi_change = delta(
            current.get("total_call_oi"),
            previous.get("total_call_oi"),
        )
        put_oi_change = delta(
            current.get("total_put_oi"),
            previous.get("total_put_oi"),
        )
        pcr_change = delta(
            current.get("pcr_oi"),
            previous.get("pcr_oi"),
        )
        iv_change = delta(
            current.get("atm_iv"),
            previous.get("atm_iv"),
        )

        total_oi_change = None
        if (
            call_oi_change is not None
            and put_oi_change is not None
        ):
            total_oi_change = (
                call_oi_change + put_oi_change
            )

        buildup = "UNCLASSIFIED"
        if (
            price_change is not None
            and total_oi_change is not None
        ):
            if price_change > 0 and total_oi_change > 0:
                buildup = "LONG_BUILDUP"
            elif price_change < 0 and total_oi_change > 0:
                buildup = "SHORT_BUILDUP"
            elif price_change > 0 and total_oi_change < 0:
                buildup = "SHORT_COVERING"
            elif price_change < 0 and total_oi_change < 0:
                buildup = "LONG_UNWINDING"
            else:
                buildup = "FLAT_MIXED"

        return {
            "status": "DELTA_AVAILABLE",
            "elapsed_from": previous.get("captured_at"),
            "price_change": price_change,
            "call_oi_change": call_oi_change,
            "put_oi_change": put_oi_change,
            "total_oi_change": total_oi_change,
            "pcr_change": pcr_change,
            "atm_iv_change": iv_change,
            "call_wall_changed": (
                previous.get("call_resistance_strike")
                != current.get("call_resistance_strike")
            ),
            "put_wall_changed": (
                previous.get("put_support_strike")
                != current.get("put_support_strike")
            ),
            "buildup_classification": buildup,
            "caution": (
                "This classification uses aggregate option-chain OI change "
                "plus underlying price change. It is contextual, not a "
                "guaranteed futures-position classification."
            ),
        }

    async def take_option_snapshot(
        self,
        symbol,
        expiry=None,
    ):
        chain = await self.option_chain(
            symbol,
            expiry,
        )
        expiry = chain["expiry"]
        key = self._snapshot_key(
            symbol,
            expiry,
        )

        current = self._snapshot_payload(
            symbol,
            expiry,
            chain["data"],
        )
        previous = self._option_snapshots.get(key)
        delta = self._snapshot_delta(
            previous,
            current,
        )

        self._option_snapshots[key] = current

        return {
            "provider": "GROWW",
            "symbol": symbol,
            "expiry": expiry,
            "snapshot": {
                k: v
                for k, v in current.items()
                if k != "rows"
            },
            "change": delta,
            "persistence": (
                "PROCESS_MEMORY_ONLY: resets if Render restarts or spins down."
            ),
        }

    async def scan(self, symbols, timeframe, min_rr):
        results = []

        for symbol in symbols:
            try:
                candles = await self.candles(
                    symbol.upper(),
                    timeframe,
                )
                results.append(
                    analyze_candles(
                        symbol.upper(),
                        candles,
                        min_rr,
                    )
                )
            except Exception as exc:
                results.append({
                    "symbol": symbol.upper(),
                    "status": "ERROR",
                    "error": str(exc),
                })

        setups = sorted(
            [
                x
                for x in results
                if x.get("status") == "SETUP"
            ],
            key=lambda x: x.get("alpha_score", 0),
            reverse=True,
        )

        return {
            "provider": "GROWW",
            "timeframe": timeframe,
            "min_risk_reward": min_rr,
            "setups": setups,
            "others": [
                x
                for x in results
                if x.get("status") != "SETUP"
            ],
            "warning": (
                "Signals are research outputs, not guaranteed profits."
            ),
        }

    async def multi_timeframe_scan(
        self,
        symbols,
        timeframes,
        min_rr,
    ):
        results = []
        weights = {
            "5m": 0.20,
            "15m": 0.35,
            "1h": 0.30,
            "1d": 0.15,
        }

        for symbol in symbols:
            tf = {}

            for timeframe in timeframes:
                try:
                    candles = await self.candles(
                        symbol.upper(),
                        timeframe,
                    )
                    tf[timeframe] = analyze_candles(
                        symbol.upper(),
                        candles,
                        min_rr,
                    )
                except Exception as exc:
                    tf[timeframe] = {
                        "symbol": symbol.upper(),
                        "status": "ERROR",
                        "error": str(exc),
                    }

            valid = [
                v
                for v in tf.values()
                if v.get("status") != "ERROR"
            ]

            if not valid:
                results.append({
                    "symbol": symbol.upper(),
                    "status": "ERROR",
                    "timeframes": tf,
                })
                continue

            weighted_sum = sum(
                tf[t].get("alpha_score", 50)
                * weights.get(t, 0.25)
                for t in tf
                if tf[t].get("status") != "ERROR"
            )
            weight_used = sum(
                weights.get(t, 0.25)
                for t in tf
                if tf[t].get("status") != "ERROR"
            )

            score = (
                weighted_sum / weight_used
                if weight_used
                else 50
            )

            long_votes = sum(
                1
                for v in valid
                if v.get("signal")
                in ("LONG", "STRONG_LONG", "WATCH_LONG")
                and v.get("alpha_score", 0) >= 58
            )
            short_votes = sum(
                1
                for v in valid
                if v.get("signal")
                in ("SHORT", "STRONG_SHORT", "WATCH_SHORT")
                and v.get("alpha_score", 100) <= 42
            )

            setup_long = sum(
                1
                for v in valid
                if v.get("status") == "SETUP"
                and v.get("direction") == "LONG"
            )
            setup_short = sum(
                1
                for v in valid
                if v.get("status") == "SETUP"
                and v.get("direction") == "SHORT"
            )

            higher_bull = any(
                tf.get(t, {}).get(
                    "alpha_score",
                    50,
                ) >= 55
                for t in ("1h", "1d")
                if t in tf
            )
            higher_bear = any(
                tf.get(t, {}).get(
                    "alpha_score",
                    50,
                ) <= 45
                for t in ("1h", "1d")
                if t in tf
            )

            n = len(valid)

            if (
                score >= 68
                and long_votes >= max(2, n - 1)
                and setup_long >= 1
                and not higher_bear
            ):
                status = "SETUP"
                direction = "LONG"
            elif (
                score <= 32
                and short_votes >= max(2, n - 1)
                and setup_short >= 1
                and not higher_bull
            ):
                status = "SETUP"
                direction = "SHORT"
            else:
                status = "NO_TRADE"
                direction = None

            if (
                status == "SETUP"
                and direction == "LONG"
            ):
                signal = (
                    "STRONG_LONG"
                    if score >= 80
                    and long_votes == n
                    else "LONG"
                )
            elif (
                status == "SETUP"
                and direction == "SHORT"
            ):
                signal = (
                    "STRONG_SHORT"
                    if score <= 20
                    and short_votes == n
                    else "SHORT"
                )
            elif score >= 58:
                signal = "WATCH_LONG"
            elif score <= 42:
                signal = "WATCH_SHORT"
            else:
                signal = "NO_TRADE"

            item = {
                "symbol": symbol.upper(),
                "status": status,
                "signal": signal,
                "multi_timeframe_score": round(
                    score,
                    1,
                ),
                "timeframe_votes": {
                    "long": long_votes,
                    "short": short_votes,
                    "valid": n,
                },
                "higher_timeframe": {
                    "bullish": higher_bull,
                    "bearish": higher_bear,
                },
                "timeframes": tf,
            }

            if status == "SETUP":
                execution = tf.get(
                    "15m",
                    valid[0],
                )
                item.update({
                    "direction": direction,
                    "execution_timeframe": (
                        "15m"
                        if "15m" in tf
                        else timeframes[0]
                    ),
                    "entry": execution.get("entry"),
                    "stop_loss": execution.get("stop_loss"),
                    "target1": execution.get("target1"),
                    "target2": execution.get("target2"),
                    "risk_reward": execution.get("risk_reward"),
                })
            else:
                item["reason"] = (
                    "Multi-timeframe alignment threshold not met"
                )

            results.append(item)

        return {
            "provider": "GROWW",
            "mode": "MULTI_TIMEFRAME",
            "timeframes": timeframes,
            "min_risk_reward": min_rr,
            "setups": [
                x
                for x in results
                if x.get("status") == "SETUP"
            ],
            "others": [
                x
                for x in results
                if x.get("status") != "SETUP"
            ],
        }

    async def market_context(self, timeframes):
        mtf = await self.multi_timeframe_scan(
            ["NIFTY"],
            timeframes,
            1.5,
        )

        result = (
            mtf.get("setups")
            or mtf.get("others")
            or [{}]
        )[0]

        score = float(
            result.get(
                "multi_timeframe_score",
                50,
            )
        )

        if score >= 60:
            bias = "BULLISH"
        elif score <= 40:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return {
            "symbol": "NIFTY",
            "bias": bias,
            "market_score": round(score, 1),
            "details": result,
        }

    async def fno_confirm(
        self,
        symbol,
        timeframes,
        min_rr,
        expiry=None,
        include_market=True,
        take_snapshot=True,
    ):
        mtf = await self.multi_timeframe_scan(
            [symbol],
            timeframes,
            min_rr,
        )

        technical = (
            mtf.get("setups")
            or mtf.get("others")
            or [{}]
        )[0]

        chain = await self.option_chain(
            symbol,
            expiry,
        )
        fno = self._fno_analytics(
            chain["data"]
        )

        market = None
        if include_market:
            market = await self.market_context(
                timeframes,
            )

        snapshot_change = None
        if take_snapshot:
            snapshot_result = await self.take_option_snapshot(
                symbol,
                chain["expiry"],
            )
            snapshot_change = snapshot_result.get(
                "change"
            )

        technical_score = float(
            technical.get(
                "multi_timeframe_score",
                50,
            )
        )
        fno_score = float(
            fno.get(
                "fno_score",
                50,
            )
        )
        market_score = (
            float(market.get("market_score", 50))
            if market
            else 50.0
        )

        overall = (
            technical_score * 0.60
            + fno_score * 0.25
            + market_score * 0.15
        )

        adjustment_reasons = []

        if market:
            if (
                technical.get("signal")
                in ("WATCH_LONG", "LONG", "STRONG_LONG")
                and market.get("bias") == "BEARISH"
            ):
                overall -= 5
                adjustment_reasons.append(
                    "Long bias penalized by bearish NIFTY context"
                )

            if (
                technical.get("signal")
                in ("WATCH_SHORT", "SHORT", "STRONG_SHORT")
                and market.get("bias") == "BULLISH"
            ):
                overall += 5
                adjustment_reasons.append(
                    "Short bias penalized by bullish NIFTY context"
                )

        overall = round(
            max(0, min(100, overall)),
            1,
        )

        return {
            "provider": "GROWW",
            "mode": "MTF_FNO_MARKET_CONFIRMATION",
            "symbol": symbol,
            "expiry": chain["expiry"],
            "overall_alpha_score": overall,
            "technical_score": round(
                technical_score,
                1,
            ),
            "fno_score": round(
                fno_score,
                1,
            ),
            "market_score": round(
                market_score,
                1,
            ),
            "status": technical.get(
                "status",
                "NO_TRADE",
            ),
            "signal": technical.get(
                "signal",
                "NO_TRADE",
            ),
            "technical": technical,
            "market_context": market,
            "fno": fno,
            "oi_change": snapshot_change,
            "score_adjustments": adjustment_reasons,
            "warning": (
                "OI change is only available after at least two snapshots "
                "within the same Render process. Process memory resets on "
                "restart/spin-down. Use a durable database in a later version."
            ),
        }
