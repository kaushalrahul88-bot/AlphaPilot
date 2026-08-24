import asyncio
import time
from collections import deque

from .groww_autoauth import AutoAuthAmountAwareGrowwProvider


class RateLimitedGrowwProvider(AutoAuthAmountAwareGrowwProvider):
    """Shared process-wide throttle for Groww-backed requests.

    The limiter intentionally stays below Groww's published ceilings so health
    checks, retries and 44-symbol universe scans cannot create short bursts that
    push the account into HTTP 429 rate limiting.
    """

    MAX_PER_SECOND = 4
    MAX_PER_MINUTE = 180
    _request_times = deque()
    _rate_lock = None

    # Market Brain v2.2 research breadth universe. These are ordinary NSE cash
    # instruments and this mapping only broadens data access; it does not change
    # any production scan, setup, risk or execution rule.
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

    async def quote(self, symbol):
        await self._throttle()
        return await super().quote(symbol)

    async def candles(self, symbol, timeframe="15m"):
        await self._throttle()
        return await super().candles(symbol, timeframe)

    async def expiries(self, symbol):
        # Groww's expiry lookup can issue more than one upstream request. Reserve
        # two slots so the option-chain path remains comfortably within budget.
        await self._throttle()
        await self._throttle()
        return await super().expiries(symbol)

    async def option_chain(self, symbol, expiry=None):
        # Reserve one slot for the chain request itself. If expiry is omitted,
        # expiries() above also reserves its own upstream-call budget.
        await self._throttle()
        return await super().option_chain(symbol, expiry)
