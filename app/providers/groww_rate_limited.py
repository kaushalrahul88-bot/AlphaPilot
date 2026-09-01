import asyncio
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .groww_autoauth import AutoAuthAmountAwareGrowwProvider
from ..crude_oil_mini_contracts import (
    CRUDE_OIL_MINI,
    fetch_crude_oil_mini_master,
    resolve_crude_oil_mini_universe,
)


IST = ZoneInfo("Asia/Kolkata")


class RateLimitedGrowwProvider(AutoAuthAmountAwareGrowwProvider):
    """Shared process-wide throttle for Groww-backed requests.

    The limiter intentionally stays below Groww's published ceilings so health
    checks, retries and 44-symbol universe scans cannot create short bursts that
    push the account into HTTP 429 rate limiting.

    CRUDEOILM is intentionally handled as a dedicated MCX family. It never
    aliases regular CRUDEOIL, and this provider-only path does not change the
    scheduled Copper/commodity collector universe.
    """

    MAX_PER_SECOND = 4
    MAX_PER_MINUTE = 180
    _request_times = deque()
    _rate_lock = None

    MARKET_BREADTH_CASH_SYMBOLS = {
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
        "AXISBANK", "KOTAKBANK", "BAJFINANCE", "LT", "BHARTIARTL", "ITC",
        "MARUTI", "M&M", "TATAMOTORS", "SUNPHARMA", "DRREDDY", "CIPLA",
        "HCLTECH", "WIPRO", "TATASTEEL", "JSWSTEEL", "HINDALCO", "ONGC",
        "NTPC", "POWERGRID", "ADANIPORTS", "TITAN", "ASIANPAINT", "ULTRACEMCO",
    }

    def _instrument(self, symbol):
        symbol = str(symbol).upper().strip()
        if symbol in self.MARKET_BREADTH_CASH_SYMBOLS:
            return "NSE", "CASH", symbol, f"NSE-{symbol}"
        return super()._instrument(symbol)

    @classmethod
    def _lock(cls):
        if cls._rate_lock is None:
            cls._rate_lock = asyncio.Lock()
        return cls._rate_lock

    @classmethod
    async def _throttle(cls):
        while True:
            wait_for = 0.0
            async with cls._lock():
                now = time.monotonic()
                while cls._request_times and now - cls._request_times[0] >= 60.0:
                    cls._request_times.popleft()
                recent_second = [t for t in cls._request_times if now - t < 1.0]
                if len(cls._request_times) < cls.MAX_PER_MINUTE and len(recent_second) < cls.MAX_PER_SECOND:
                    cls._request_times.append(now)
                    return
                if len(recent_second) >= cls.MAX_PER_SECOND:
                    wait_for = max(wait_for, 1.02 - (now - recent_second[0]))
                if len(cls._request_times) >= cls.MAX_PER_MINUTE:
                    wait_for = max(wait_for, 60.05 - (now - cls._request_times[0]))
            await asyncio.sleep(max(0.05, wait_for))

    @staticmethod
    def _mini_symbol(symbol):
        return str(symbol or "").upper().strip().startswith(CRUDE_OIL_MINI)

    @staticmethod
    def _mini_contract_from_rows(rows, symbol, as_of=None):
        wanted = str(symbol or "").upper().strip()
        observed = as_of or datetime.now(IST)
        if wanted == CRUDE_OIL_MINI:
            return dict(resolve_crude_oil_mini_universe(rows, observed)["future"])
        exact = [
            dict(row) for row in rows or []
            if str(row.get("underlying") or "").upper() == CRUDE_OIL_MINI
            and str(row.get("trading_symbol") or "").upper() == wanted
        ]
        if len(exact) != 1:
            raise ValueError(
                f"{wanted} is not one exact currently listed {CRUDE_OIL_MINI} contract"
            )
        return exact[0]

    async def _mini_contract(self, symbol):
        rows = await fetch_crude_oil_mini_master()
        return self._mini_contract_from_rows(rows, symbol)

    @staticmethod
    def _raw_timestamp_key(value):
        try:
            if isinstance(value, (int, float)) or str(value).strip().isdigit():
                number = float(value)
                if number > 1e12:
                    number /= 1000.0
                return datetime.fromtimestamp(number, IST).isoformat()
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=IST)
            return parsed.astimezone(IST).isoformat()
        except Exception:
            return str(value)

    @classmethod
    def _normalize_mini_candle(cls, row):
        """Return one Mini candle with a canonical offset-aware IST timestamp.

        Groww's legacy MCX history can emit epoch seconds while the modern route
        emits ISO timestamps. The Market Brain must never receive a mixed time
        representation, so normalization happens before dedupe and before data
        leaves the provider.
        """
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            return None
        timestamp = cls._raw_timestamp_key(row[0])
        normalized = list(row)
        normalized[0] = timestamp
        return timestamp, normalized

    @staticmethod
    def _response_candles(response):
        if response.status_code != 200:
            return []
        body = response.json()
        payload = body.get("payload", body) if isinstance(body, dict) else {}
        return payload.get("candles", []) if isinstance(payload, dict) else []

    async def _mini_fetch_chunk(
        self,
        contract,
        *,
        candle_interval,
        legacy_minutes,
        start,
        end,
        tolerate_legacy_miss=False,
    ):
        """Use Groww modern history first, then legacy range for the same contract.

        Exact Mini options can legitimately have no data before their listing
        boundary. In that case Groww may answer the modern route with a clean
        empty payload while the legacy route rejects the older interval. Only
        CE/PE callers may tolerate that legacy-only miss. A modern-route error,
        or the same condition for futures, still fails closed.
        """
        headers = await self._headers()
        modern_params = {
            "exchange": "MCX",
            "segment": "COMMODITY",
            "groww_symbol": contract.get("groww_symbol"),
            "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
            "candle_interval": candle_interval,
        }
        await self._throttle()
        async with httpx.AsyncClient(timeout=40) as client:
            modern = await client.get(
                f"{self.BASE_URL}/v1/historical/candles",
                headers=headers,
                params=modern_params,
            )
        candles = self._response_candles(modern)
        if candles:
            return candles

        legacy_params = {
            "exchange": "MCX",
            "segment": "COMMODITY",
            "trading_symbol": contract.get("trading_symbol"),
            "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
            "interval_in_minutes": str(int(legacy_minutes)),
        }
        await self._throttle()
        async with httpx.AsyncClient(timeout=40) as client:
            legacy = await client.get(
                f"{self.BASE_URL}/v1/historical/candle/range",
                headers=headers,
                params=legacy_params,
            )
        legacy_candles = self._response_candles(legacy)
        if legacy_candles:
            return legacy_candles

        if modern.status_code != 200:
            modern.raise_for_status()
        if legacy.status_code != 200:
            if tolerate_legacy_miss:
                return []
            legacy.raise_for_status()
        return []

    async def _mini_candles(self, symbol, timeframe="15m"):
        contract = await self._mini_contract(symbol)
        interval_map = {
            "5m": ("5minute", 5, 180, 7),
            "15m": ("15minute", 15, 180, 14),
            "1h": ("1hour", 60, 180, 30),
            "1d": ("1day", 1440, 365, 90),
        }
        candle_interval, interval_minutes, future_lookback_days, chunk_days = interval_map.get(
            timeframe, ("15minute", 15, 180, 14)
        )
        instrument_type = str(contract.get("instrument_type") or "").upper()
        is_option = instrument_type in {"CE", "PE"}
        lookback_days = 63 if is_option else future_lookback_days
        now = datetime.now(IST)
        start = now - timedelta(days=lookback_days)
        cursor = start
        step = timedelta(minutes=interval_minutes)
        deduplicated = {}

        while cursor <= now:
            chunk_end = min(now, cursor + timedelta(days=chunk_days) - step)
            candles = await self._mini_fetch_chunk(
                contract,
                candle_interval=candle_interval,
                legacy_minutes=interval_minutes,
                start=cursor,
                end=chunk_end,
                tolerate_legacy_miss=is_option,
            )
            for row in candles or []:
                normalized = self._normalize_mini_candle(row)
                if normalized is None:
                    continue
                timestamp, normalized_row = normalized
                deduplicated[timestamp] = normalized_row
            if chunk_end >= now:
                break
            cursor = chunk_end + step

        return [deduplicated[key] for key in sorted(deduplicated)]

    async def _mini_quote(self, symbol):
        contract = await self._mini_contract(symbol)
        await self._throttle()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.BASE_URL}/v1/live-data/quote",
                headers=await self._headers(),
                params={
                    "exchange": "MCX",
                    "segment": "COMMODITY",
                    "trading_symbol": contract["trading_symbol"],
                },
            )
        response.raise_for_status()
        return {
            "provider": "GROWW",
            "symbol": str(symbol).upper().strip(),
            "exchange": "MCX",
            "segment": "COMMODITY",
            "contract": contract,
            "data": response.json(),
        }

    async def quote(self, symbol):
        if self._mini_symbol(symbol):
            return await self._mini_quote(symbol)
        await self._throttle()
        return await super().quote(symbol)

    async def candles(self, symbol, timeframe="15m"):
        if self._mini_symbol(symbol):
            return await self._mini_candles(symbol, timeframe)
        await self._throttle()
        return await super().candles(symbol, timeframe)

    async def expiries(self, symbol):
        await self._throttle()
        await self._throttle()
        return await super().expiries(symbol)

    async def option_chain(self, symbol, expiry=None):
        await self._throttle()
        return await super().option_chain(symbol, expiry)
