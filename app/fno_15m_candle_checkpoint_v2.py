"""Authenticated durable candle checkpoints for the F&O historical replay.

The trading methodology is unchanged. This module only makes reconstructible
historical-candle acquisition safe against an expired static Groww access token.
Groww access tokens expire daily; when a historical request returns 401 and API
key/secret credentials are configured, the replay switches this provider
instance to the key/secret token flow and retries once. Authentication failures
are never accepted as an empty technical history.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Awaitable, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from . import fno_15m_historical_replay_v1 as core
from .fno_15m_restart_safe_replay import (
    dataset_key,
    ensure_cache_schema,
    load_cached_history,
    save_cached_history,
)

IST = ZoneInfo("Asia/Kolkata")
ProgressCallback = Callable[[Mapping[str, Any]], Awaitable[None]]


class HistoricalAuthenticationError(RuntimeError):
    """Historical data cannot be authenticated without user/account action."""


def _is_auth_error(exc: BaseException | str | None) -> bool:
    text = str(exc or "").lower()
    return (
        "401 unauthorized" in text
        or "historical http 401" in text
        or "status_code=401" in text
    )


async def _refresh_after_401(provider) -> None:
    api_key = str(getattr(provider, "api_key", "") or "").strip()
    api_secret = str(getattr(provider, "api_secret", "") or "").strip()
    if not api_key or not api_secret:
        raise HistoricalAuthenticationError(
            "GROWW_HISTORICAL_AUTH_401: static access token is no longer "
            "authorised and GROWW_API_KEY/GROWW_API_SECRET are not both "
            "configured for automatic token regeneration"
        )

    # Limit the change to this provider instance. Other AlphaPilot workers keep
    # their own auth objects. Clearing access_token lets the existing provider
    # use its documented API-key/secret approval flow.
    provider.access_token = ""
    provider._cached_token = None
    try:
        token = await provider._get_access_token()
    except Exception as exc:
        raise HistoricalAuthenticationError(
            "GROWW_HISTORICAL_AUTH_REFRESH_FAILED: API-key/secret token "
            f"generation was not authorised ({exc.__class__.__name__}: "
            f"{str(exc)[:320]})"
        ) from exc
    if not str(token or "").strip():
        raise HistoricalAuthenticationError(
            "GROWW_HISTORICAL_AUTH_REFRESH_FAILED: token generation returned "
            "an empty token"
        )


async def _fetch_one_history(
    provider,
    symbol: str,
    timeframe: str,
    start: datetime,
    latest: datetime,
) -> list[list]:
    try:
        return await core.fetch_historical_candles(
            provider,
            symbol,
            timeframe,
            start,
            latest,
        )
    except Exception as first:
        if not _is_auth_error(first):
            raise
        await _refresh_after_401(provider)
        try:
            return await core.fetch_historical_candles(
                provider,
                symbol,
                timeframe,
                start,
                latest,
            )
        except Exception as second:
            if _is_auth_error(second):
                raise HistoricalAuthenticationError(
                    "GROWW_HISTORICAL_AUTH_401_AFTER_REFRESH: regenerated "
                    "credentials are still not authorised for historical data"
                ) from second
            raise


async def _progress(
    callback: ProgressCallback | None,
    payload: Mapping[str, Any],
) -> None:
    if callback is not None:
        await callback(dict(payload))


async def fetch_all_histories_checkpointed_v2(
    provider,
    symbols: Iterable[str],
    trade_dates: list,
    database_url: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> tuple[dict[str, dict[str, list[list]]], list[dict[str, Any]], str]:
    """Fetch histories with durable checkpoints and auth-safe retry semantics.

    Successful cached histories are reused. Cached failures are deliberately
    retried so an expired credential can never permanently poison a dataset key.
    A 401 is fatal if the configured refresh path cannot authenticate; the replay
    must not continue with empty technical histories in that case.
    """
    symbols = list(symbols)
    if not trade_dates:
        return {}, [], dataset_key([], symbols)

    await ensure_cache_schema(database_url)
    key = dataset_key(trade_dates, symbols)
    earliest = datetime.combine(trade_dates[0], time(9, 15), tzinfo=IST)
    latest = datetime.combine(trade_dates[-1], time(15, 30), tzinfo=IST)
    histories: dict[str, dict[str, list[list]]] = {}
    failures: list[dict[str, Any]] = []
    total = len(symbols) * len(core.TIMEFRAMES)
    completed = 0
    cache_hits = 0
    retried_cached_errors = 0

    for symbol in symbols:
        histories[symbol] = {}
        for timeframe in core.TIMEFRAMES:
            cached = await load_cached_history(database_url, key, symbol, timeframe)
            use_cache = cached is not None and cached[1] is None
            if use_cache:
                candles, _ = cached
                histories[symbol][timeframe] = candles
                cache_hits += 1
            else:
                if cached is not None and cached[1] is not None:
                    retried_cached_errors += 1
                start = earliest - timedelta(days=core.LOOKBACK_DAYS[timeframe])
                error = None
                try:
                    candles = await _fetch_one_history(
                        provider,
                        symbol,
                        timeframe,
                        start,
                        latest,
                    )
                except HistoricalAuthenticationError:
                    # Do not overwrite the durable run with a fake empty history.
                    # The worker will persist this explicit blocker as FAILED.
                    raise
                except Exception as exc:
                    candles = []
                    error = f"{exc.__class__.__name__}: {str(exc)[:480]}"
                    failures.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "error": error,
                        "from_cache": False,
                    })
                histories[symbol][timeframe] = candles
                await save_cached_history(
                    database_url,
                    key,
                    symbol,
                    timeframe,
                    candles,
                    error,
                )

            completed += 1
            await _progress(progress_callback, {
                "stage": "HISTORICAL_CANDLE_CHECKPOINTS",
                "dataset_key": key,
                "completed_histories": completed,
                "total_histories": total,
                "cache_hits": cache_hits,
                "retried_cached_errors": retried_cached_errors,
                "last_symbol": symbol,
                "last_timeframe": timeframe,
            })

    return histories, failures, key


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_15M_CANDLE_CHECKPOINT_V2_AUTH_SAFE",
        "successful_cache_entries_reused": True,
        "cached_errors_retried": True,
        "historical_401_may_trigger_key_secret_refresh": True,
        "historical_401_never_becomes_empty_valid_history": True,
        "strategy_logic_changed": False,
        "live_execution": False,
        "capital_committed": 0,
    }
