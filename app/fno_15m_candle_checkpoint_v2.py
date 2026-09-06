"""Authenticated durable candle checkpoints for the F&O historical replay.

The trading methodology is unchanged. This module only makes reconstructible
historical-candle acquisition safe against an expired static Groww access token.
When a historical request returns 401, the replay may switch this provider to an
already-configured unattended dynamic credential path (TOTP first, then the
API-key/secret approval flow) and retry once. Authentication failures are never
accepted as an empty technical history.
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
        or "status code 401" in text
    )


def _dynamic_auth_mode(provider) -> str | None:
    totp_token = str(getattr(provider, "totp_token", "") or "").strip()
    totp_secret = str(getattr(provider, "totp_secret", "") or "").strip()
    if totp_token and totp_secret:
        return "TOTP"

    api_key = str(getattr(provider, "api_key", "") or "").strip()
    api_secret = str(getattr(provider, "api_secret", "") or "").strip()
    if api_key and api_secret:
        return "API_KEY_SECRET"
    return None


def _clear_stale_auth_state(provider) -> None:
    """Drop only process-local stale session state before dynamic regeneration."""
    provider.access_token = ""
    if hasattr(provider, "_cached_token"):
        provider._cached_token = None
    if hasattr(provider, "_cached_auth_session"):
        provider._cached_auth_session = None

    # _get_access_token() can otherwise reuse an inherited process-shared token
    # for the current Groww session. Clearing the concrete provider class ensures
    # the retry actually regenerates credentials instead of replaying the 401.
    cls = provider.__class__
    if hasattr(cls, "_shared_token"):
        cls._shared_token = None
    if hasattr(cls, "_shared_auth_session"):
        cls._shared_auth_session = None


async def _refresh_after_401(provider) -> str:
    auth_mode = _dynamic_auth_mode(provider)
    if auth_mode is None:
        raise HistoricalAuthenticationError(
            "GROWW_HISTORICAL_AUTH_401: configured static access token is no "
            "longer authorised and no complete dynamic Groww credential pair "
            "is available (GROWW_TOTP_TOKEN + GROWW_TOTP_SECRET or "
            "GROWW_API_KEY + GROWW_API_SECRET)"
        )

    _clear_stale_auth_state(provider)
    try:
        token = await provider._get_access_token()
    except Exception as exc:
        raise HistoricalAuthenticationError(
            "GROWW_HISTORICAL_AUTH_REFRESH_FAILED: "
            f"{auth_mode} token generation was not authorised "
            f"({exc.__class__.__name__}: {str(exc)[:320]})"
        ) from exc
    if not str(token or "").strip():
        raise HistoricalAuthenticationError(
            "GROWW_HISTORICAL_AUTH_REFRESH_FAILED: token generation returned "
            "an empty token"
        )
    return auth_mode


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
                    # Never overwrite an authentication blocker with an empty
                    # history. The durable run worker records the explicit error.
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
        "historical_401_dynamic_refresh_modes": ["TOTP", "API_KEY_SECRET"],
        "stale_process_shared_auth_state_cleared_before_refresh": True,
        "historical_401_never_becomes_empty_valid_history": True,
        "auth_failure_is_fatal_to_replay": True,
        "strategy_logic_changed": False,
        "live_execution": False,
        "capital_committed": 0,
    }
