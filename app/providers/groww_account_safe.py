from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from .groww_rate_limited import RateLimitedGrowwProvider


T = TypeVar("T")


class AccountSafeGrowwProvider(RateLimitedGrowwProvider):
    """Conservative account-wide guard for Groww-backed AlphaPilot traffic.

    Multiple scheduled AlphaPilot collectors share the same Groww account and
    Render worker. The previous local ceiling (180/minute, 4/second) allowed
    independent workflows to collectively reach Groww's upstream limiter. A 429
    then caused the live-shadow click to retry the same request while the account
    was still cooling down.

    This subclass keeps the existing process-wide queue but deliberately lowers
    the admission rate and installs a shared cooldown whenever Groww returns 429.
    The cooldown is prospective operational protection only; it does not alter
    any market signal, historical observation, or decision rule.
    """

    MAX_PER_SECOND = 2
    MAX_PER_MINUTE = 45
    RATE_LIMIT_COOLDOWN_SECONDS = 70.0
    _blocked_until = 0.0
    _cooldown_lock: asyncio.Lock | None = None

    @classmethod
    def _cooldown_guard(cls) -> asyncio.Lock:
        if cls._cooldown_lock is None:
            cls._cooldown_lock = asyncio.Lock()
        return cls._cooldown_lock

    @classmethod
    async def _wait_for_cooldown(cls) -> None:
        while True:
            remaining = cls._blocked_until - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 5.0))

    @classmethod
    async def _throttle(cls) -> None:
        await cls._wait_for_cooldown()
        await super()._throttle()
        # A 429 may have been observed while this coroutine waited in the parent
        # queue. Re-check before allowing the upstream request to leave.
        await cls._wait_for_cooldown()

    @classmethod
    async def _register_rate_limit(cls) -> None:
        async with cls._cooldown_guard():
            cls._blocked_until = max(
                cls._blocked_until,
                time.monotonic() + cls.RATE_LIMIT_COOLDOWN_SECONDS,
            )

    @classmethod
    async def _guarded(cls, operation: Callable[[], Awaitable[T]]) -> T:
        try:
            return await operation()
        except httpx.HTTPStatusError as exc:
            if getattr(exc.response, "status_code", None) == 429:
                await cls._register_rate_limit()
            raise

    async def quote(self, symbol):
        return await self._guarded(lambda: super(AccountSafeGrowwProvider, self).quote(symbol))

    async def candles(self, symbol, timeframe="15m"):
        return await self._guarded(
            lambda: super(AccountSafeGrowwProvider, self).candles(symbol, timeframe)
        )

    async def expiries(self, symbol):
        return await self._guarded(lambda: super(AccountSafeGrowwProvider, self).expiries(symbol))

    async def option_chain(self, symbol, expiry=None):
        return await self._guarded(
            lambda: super(AccountSafeGrowwProvider, self).option_chain(symbol, expiry)
        )
