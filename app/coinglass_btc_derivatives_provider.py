"""Optional CoinGlass BTC derivatives provider for point-in-time research capture.

The provider uses only documented CoinGlass V4 endpoints for aggregated BTC
futures open interest and liquidations. It is disabled by default, requires an
explicit API key, never executes trades, and treats provider history fetched now
as a first-seen observation now rather than pretending it was available to
AlphaPilot in the past.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any

import httpx

OI_AGGREGATED_HISTORY_URL = "https://open-api-v4.coinglass.com/api/futures/open-interest/aggregated-history"
LIQUIDATION_AGGREGATED_HISTORY_URL = "https://open-api-v4.coinglass.com/api/futures/liquidation/aggregated-history"
SUPPORTED_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "6h", "8h", "12h", "1d", "1w"}
DEFAULT_EXCHANGES = ("Binance", "OKX", "Bybit")


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite(name: str, value: Any, *, nonnegative: bool = False, positive: bool = False) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be >= 0")
    if positive and number <= 0:
        raise ValueError(f"{name} must be > 0")
    return number


def _provider_time(value: Any) -> datetime:
    number = _finite("provider time", value, positive=True)
    return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)


@dataclass(frozen=True)
class CoinGlassBtcDerivativesPolicy:
    enabled: bool = False
    api_key: str = ""
    timeout_seconds: float = 10.0
    interval: str = "4h"
    exchanges: tuple[str, ...] = DEFAULT_EXCHANGES

    def validated(self) -> "CoinGlassBtcDerivativesPolicy":
        if self.enabled and not str(self.api_key or "").strip():
            raise ValueError("CoinGlass capture enabled but CG-API-KEY is missing")
        if self.interval not in SUPPORTED_INTERVALS:
            raise ValueError("unsupported CoinGlass interval")
        if not isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        normalized = tuple(str(value).strip() for value in self.exchanges if str(value).strip())
        if not normalized:
            raise ValueError("at least one CoinGlass liquidation exchange is required")
        if len(set(normalized)) != len(normalized):
            raise ValueError("CoinGlass exchanges must be unique")
        return self


@dataclass(frozen=True)
class CoinGlassOpenInterestCapture:
    first_seen_at: datetime
    provider_time: datetime
    interval: str
    open_interest_open_usd: float
    open_interest_high_usd: float
    open_interest_low_usd: float
    open_interest_close_usd: float
    symbol: str = "BTC"
    unit: str = "usd"
    provider: str = "COINGLASS_V4"

    def validated(self) -> "CoinGlassOpenInterestCapture":
        if self.interval not in SUPPORTED_INTERVALS:
            raise ValueError("unsupported CoinGlass interval")
        if _utc(self.provider_time) > _utc(self.first_seen_at):
            raise ValueError("provider_time cannot be after first_seen_at")
        values = [
            _finite("open_interest_open_usd", self.open_interest_open_usd, nonnegative=True),
            _finite("open_interest_high_usd", self.open_interest_high_usd, nonnegative=True),
            _finite("open_interest_low_usd", self.open_interest_low_usd, nonnegative=True),
            _finite("open_interest_close_usd", self.open_interest_close_usd, nonnegative=True),
        ]
        if values[1] < max(values[0], values[2], values[3]) or values[2] > min(values[0], values[1], values[3]):
            raise ValueError("invalid open-interest OHLC geometry")
        return self


@dataclass(frozen=True)
class CoinGlassLiquidationCapture:
    first_seen_at: datetime
    provider_time: datetime
    interval: str
    long_liquidation_usd: float
    short_liquidation_usd: float
    exchanges: tuple[str, ...]
    symbol: str = "BTC"
    provider: str = "COINGLASS_V4"

    def validated(self) -> "CoinGlassLiquidationCapture":
        if self.interval not in SUPPORTED_INTERVALS:
            raise ValueError("unsupported CoinGlass interval")
        if _utc(self.provider_time) > _utc(self.first_seen_at):
            raise ValueError("provider_time cannot be after first_seen_at")
        _finite("long_liquidation_usd", self.long_liquidation_usd, nonnegative=True)
        _finite("short_liquidation_usd", self.short_liquidation_usd, nonnegative=True)
        if not self.exchanges:
            raise ValueError("liquidation exchange list is required")
        return self


class CoinGlassBtcDerivativesProvider:
    def __init__(self, policy: CoinGlassBtcDerivativesPolicy | None = None, client: httpx.Client | None = None):
        self.policy = (policy or CoinGlassBtcDerivativesPolicy()).validated()
        self._client = client

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("CoinGlass BTC derivatives collection is disabled by policy")

    def _get_json(self, url: str, *, params: dict) -> dict:
        self._require_enabled()
        headers = {"CG-API-KEY": self.policy.api_key}
        if self._client is not None:
            response = self._client.get(url, params=params, headers=headers, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or str(payload.get("code")) != "0" or not isinstance(payload.get("data"), list):
            raise ValueError("invalid CoinGlass response payload")
        return payload

    @staticmethod
    def _latest_visible_row(payload: dict, *, first_seen_at: datetime) -> dict:
        cutoff = _utc(first_seen_at)
        rows = []
        for raw in payload.get("data", []):
            if not isinstance(raw, dict) or raw.get("time") is None:
                continue
            stamp = _provider_time(raw["time"])
            if stamp <= cutoff:
                rows.append((stamp, raw))
        if not rows:
            raise ValueError("CoinGlass response contains no row visible by first_seen_at")
        rows.sort(key=lambda item: item[0])
        return rows[-1][1]

    def capture_open_interest(self, *, first_seen_at: datetime) -> CoinGlassOpenInterestCapture:
        first_seen = _utc(first_seen_at)
        payload = self._get_json(
            OI_AGGREGATED_HISTORY_URL,
            params={
                "symbol": "BTC",
                "interval": self.policy.interval,
                "limit": 2,
                "end_time": int(first_seen.timestamp() * 1000),
                "unit": "usd",
            },
        )
        raw = self._latest_visible_row(payload, first_seen_at=first_seen)
        return CoinGlassOpenInterestCapture(
            first_seen_at=first_seen,
            provider_time=_provider_time(raw["time"]),
            interval=self.policy.interval,
            open_interest_open_usd=_finite("open", raw["open"], nonnegative=True),
            open_interest_high_usd=_finite("high", raw["high"], nonnegative=True),
            open_interest_low_usd=_finite("low", raw["low"], nonnegative=True),
            open_interest_close_usd=_finite("close", raw["close"], nonnegative=True),
        ).validated()

    def capture_liquidations(self, *, first_seen_at: datetime) -> CoinGlassLiquidationCapture:
        first_seen = _utc(first_seen_at)
        payload = self._get_json(
            LIQUIDATION_AGGREGATED_HISTORY_URL,
            params={
                "exchange_list": ",".join(self.policy.exchanges),
                "symbol": "BTC",
                "interval": self.policy.interval,
                "limit": 2,
                "end_time": int(first_seen.timestamp() * 1000),
            },
        )
        raw = self._latest_visible_row(payload, first_seen_at=first_seen)
        return CoinGlassLiquidationCapture(
            first_seen_at=first_seen,
            provider_time=_provider_time(raw["time"]),
            interval=self.policy.interval,
            long_liquidation_usd=_finite("aggregated_long_liquidation_usd", raw["aggregated_long_liquidation_usd"], nonnegative=True),
            short_liquidation_usd=_finite("aggregated_short_liquidation_usd", raw["aggregated_short_liquidation_usd"], nonnegative=True),
            exchanges=self.policy.exchanges,
        ).validated()


def architecture_contract() -> dict:
    return {
        "version": "COINGLASS_BTC_DERIVATIVES_PROVIDER_V1",
        "provider": "COINGLASS_V4",
        "collection_enabled_by_default": False,
        "api_key_required_when_enabled": True,
        "documented_open_interest_endpoint": OI_AGGREGATED_HISTORY_URL,
        "documented_liquidation_endpoint": LIQUIDATION_AGGREGATED_HISTORY_URL,
        "historical_fetch_is_automatically_treated_as_historical_pit": False,
        "capture_first_seen_at_is_now": True,
        "open_interest_inferred_from_volume": False,
        "liquidations_inferred_from_price": False,
        "options_trade_generation_allowed": False,
        "futures_trade_generation_allowed": False,
        "broker_execution_enabled": False,
        "research_only": True,
    }
