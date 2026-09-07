from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .commodity_backtest import _fetch_chunked
from .fno_15m_candle_checkpoint_v2 import _refresh_after_401


FetchChunked = Callable[..., Awaitable[list[list]]]


async def _throttle_if_available(provider) -> None:
    throttle = getattr(provider, "_throttle", None)
    if callable(throttle):
        await throttle()


async def _register_rate_limit_if_available(provider) -> None:
    register = getattr(provider, "_register_rate_limit", None)
    if callable(register):
        await register()


async def fetch_chunked_auth_safe(
    provider,
    contract,
    interval_minutes,
    start,
    end,
    *,
    fetcher: FetchChunked | None = None,
):
    """Fetch commodity history with one fail-closed Groww auth refresh retry.

    The legacy commodity helper sends raw historical HTTP requests, so it does not
    automatically inherit AccountSafeGrowwProvider's guarded 401/429 handling.
    This wrapper keeps the existing candle-fetch semantics unchanged while adding
    only operational protection for the live PIT collector:

    - enter the shared account throttle before each fetch attempt;
    - register an account-wide cooldown if the raw helper returns HTTP 429;
    - on the first HTTP 401, clear stale session credentials through the already
      tested Groww historical refresh path and retry the exact same fetch once;
    - if the retry still fails, propagate the upstream error rather than accepting
      an empty or partial tape.

    ``fetcher`` exists only to preserve the existing Copper PIT test seam; runtime
    defaults to the unchanged commodity ``_fetch_chunked`` implementation.
    """
    candle_fetcher = fetcher or _fetch_chunked

    async def attempt():
        await _throttle_if_available(provider)
        return await candle_fetcher(
            provider,
            contract,
            interval_minutes,
            start,
            end,
        )

    try:
        return await attempt()
    except httpx.HTTPStatusError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 429:
            await _register_rate_limit_if_available(provider)
            raise
        if status != 401:
            raise

    await _refresh_after_401(provider)

    try:
        return await attempt()
    except httpx.HTTPStatusError as exc:
        if getattr(exc.response, "status_code", None) == 429:
            await _register_rate_limit_if_available(provider)
        raise


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "COMMODITY_HISTORY_AUTH_SAFE_V1",
        "historical_fetch_semantics_changed": False,
        "strategy_rules_changed": False,
        "contract_selection_changed": False,
        "pit_visibility_changed": False,
        "retry_on_401": 1,
        "shared_throttle_used": True,
        "shared_429_cooldown_registered": True,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
    }
