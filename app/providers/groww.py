import os
import time
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.engine import analyze_candles


class GrowwProvider:
    BASE_URL = "https://api.groww.in"

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
            raise RuntimeError(f"Groww token generation failed: {data}")

        self._cached_token = token
        return token

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
        if symbol not in mapping:
            raise ValueError(
                f"{symbol} is not mapped for historical scanning yet"
            )
        return mapping[symbol]

    async def quote(self, symbol):
        token = await self._get_access_token()
        exchange, segment, trading_symbol, _ = self._instrument(symbol)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-API-VERSION": "1.0",
        }
        params = {
            "exchange": exchange,
            "segment": segment,
            "trading_symbol": trading_symbol,
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.BASE_URL}/v1/live-data/quote",
                headers=headers,
                params=params,
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
        token = await self._get_access_token()
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

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-API-VERSION": "1.0",
        }
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
                headers=headers,
                params=params,
            )

        response.raise_for_status()
        data = response.json()
        payload = data.get("payload", data)
        return payload.get("candles", [])

    async def option_chain(self, symbol, expiry=None):
        return {
            "provider": "GROWW",
            "symbol": symbol,
            "expiry": expiry,
            "status": "not_implemented_yet",
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
                result
                for result in results
                if result.get("status") == "SETUP"
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
                result
                for result in results
                if result.get("status") != "SETUP"
            ],
            "warning": (
                "Signals are research outputs, not guaranteed profits. "
                "Paper-test before real-money use."
            ),
        }

    async def multi_timeframe_scan(
        self,
        symbols,
        timeframes,
        min_rr,
    ):
        results = []

        for symbol in symbols:
            tf_results = {}

            for timeframe in timeframes:
                try:
                    candles = await self.candles(
                        symbol.upper(),
                        timeframe,
                    )
                    tf_results[timeframe] = analyze_candles(
                        symbol.upper(),
                        candles,
                        min_rr,
                    )
                except Exception as exc:
                    tf_results[timeframe] = {
                        "symbol": symbol.upper(),
                        "status": "ERROR",
                        "error": str(exc),
                    }

            valid = [
                result
                for result in tf_results.values()
                if result.get("status") != "ERROR"
            ]

            if not valid:
                results.append({
                    "symbol": symbol.upper(),
                    "status": "ERROR",
                    "timeframes": tf_results,
                })
                continue

            weights = {
                "5m": 0.20,
                "15m": 0.35,
                "1h": 0.30,
                "1d": 0.15,
            }

            weighted_sum = 0.0
            weight_used = 0.0

            for timeframe, result in tf_results.items():
                if result.get("status") == "ERROR":
                    continue
                weight = weights.get(timeframe, 0.25)
                weighted_sum += result.get(
                    "alpha_score",
                    50,
                ) * weight
                weight_used += weight

            mtf_score = (
                weighted_sum / weight_used
                if weight_used
                else 50.0
            )

            long_votes = sum(
                1
                for result in valid
                if result.get("signal")
                in ("LONG", "STRONG_LONG", "WATCH_LONG")
                and result.get("alpha_score", 0) >= 58
            )
            short_votes = sum(
                1
                for result in valid
                if result.get("signal")
                in ("SHORT", "STRONG_SHORT", "WATCH_SHORT")
                and result.get("alpha_score", 100) <= 42
            )

            strong_long_votes = sum(
                1
                for result in valid
                if result.get("status") == "SETUP"
                and result.get("direction") == "LONG"
            )
            strong_short_votes = sum(
                1
                for result in valid
                if result.get("status") == "SETUP"
                and result.get("direction") == "SHORT"
            )

            higher_tf_bullish = any(
                tf_results.get(tf, {}).get(
                    "alpha_score",
                    50,
                ) >= 55
                for tf in ("1h", "1d")
                if tf in tf_results
            )
            higher_tf_bearish = any(
                tf_results.get(tf, {}).get(
                    "alpha_score",
                    50,
                ) <= 45
                for tf in ("1h", "1d")
                if tf in tf_results
            )

            total_tf = len(valid)

            if (
                mtf_score >= 68
                and long_votes >= max(2, total_tf - 1)
                and strong_long_votes >= 1
                and not higher_tf_bearish
            ):
                direction = "LONG"
                status = "SETUP"
            elif (
                mtf_score <= 32
                and short_votes >= max(2, total_tf - 1)
                and strong_short_votes >= 1
                and not higher_tf_bullish
            ):
                direction = "SHORT"
                status = "SETUP"
            else:
                direction = None
                status = "NO_TRADE"

            if status == "SETUP" and direction == "LONG":
                signal = (
                    "STRONG_LONG"
                    if mtf_score >= 80
                    and long_votes == total_tf
                    else "LONG"
                )
            elif status == "SETUP" and direction == "SHORT":
                signal = (
                    "STRONG_SHORT"
                    if mtf_score <= 20
                    and short_votes == total_tf
                    else "SHORT"
                )
            elif mtf_score >= 58:
                signal = "WATCH_LONG"
            elif mtf_score <= 42:
                signal = "WATCH_SHORT"
            else:
                signal = "NO_TRADE"

            execution_tf = (
                "15m"
                if "15m" in tf_results
                else timeframes[0]
            )
            execution = tf_results.get(
                execution_tf,
                {},
            )

            item = {
                "symbol": symbol.upper(),
                "status": status,
                "signal": signal,
                "multi_timeframe_score": round(
                    mtf_score,
                    1,
                ),
                "timeframe_votes": {
                    "long": long_votes,
                    "short": short_votes,
                    "valid": total_tf,
                },
                "higher_timeframe": {
                    "bullish": higher_tf_bullish,
                    "bearish": higher_tf_bearish,
                },
                "timeframes": tf_results,
            }

            if status == "SETUP":
                item.update({
                    "direction": direction,
                    "execution_timeframe": execution_tf,
                    "entry": execution.get("entry"),
                    "stop_loss": execution.get(
                        "stop_loss"
                    ),
                    "target1": execution.get("target1"),
                    "target2": execution.get("target2"),
                    "risk_reward": execution.get(
                        "risk_reward"
                    ),
                })
            else:
                item["reason"] = (
                    "Multi-timeframe alignment threshold not met"
                )

            results.append(item)

        setups = sorted(
            [
                result
                for result in results
                if result.get("status") == "SETUP"
            ],
            key=lambda x: x.get(
                "multi_timeframe_score",
                0,
            ),
            reverse=True,
        )

        return {
            "provider": "GROWW",
            "mode": "MULTI_TIMEFRAME",
            "timeframes": timeframes,
            "min_risk_reward": min_rr,
            "setups": setups,
            "others": [
                result
                for result in results
                if result.get("status") != "SETUP"
            ],
            "warning": (
                "Multi-timeframe scores are confluence rankings, "
                "not probabilities of profit. Validate with "
                "backtesting and paper trading."
            ),
        }
