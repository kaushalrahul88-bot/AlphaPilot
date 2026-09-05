"""CoinDCX BTC public-data provider for Crypto Brain research.

No network request is made at import time and collection is disabled by default.
The provider exposes only currently documented public surfaces used by the BTC
research stack: Spot candles, Futures candles, and current Futures RT prices.
Historical Options support is intentionally absent because it is not confirmed
by the public API documentation used for this implementation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any

import httpx

from app.crypto_btc_historical_data_adapter import (
    BtcSpotCandleArchiveRow,
    HistoricalProvenance,
    normalize_coindcx_spot_candles,
)
from app.crypto_market_intelligence import Evidence

SPOT_CANDLES_URL = "https://api.coindcx.com/market_data/candles"
FUTURES_CANDLES_URL = "https://public.coindcx.com/market_data/candlesticks"
FUTURES_CURRENT_PRICES_URL = "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
BTC_USDT_PAIR = "B-BTC_USDT"

SPOT_INTERVALS = {"1m": 60, "15m": 15 * 60, "1h": 60 * 60, "1d": 24 * 60 * 60}
FUTURES_RESOLUTIONS = {"1": 60, "5": 5 * 60, "60": 60 * 60, "1D": 24 * 60 * 60}


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not isfinite(number):
        raise ValueError("numeric field must be finite")
    return number


def _ms(value: Any) -> datetime | None:
    if value is None:
        return None
    number = float(value)
    if not isfinite(number) or number <= 0:
        return None
    return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)


@dataclass(frozen=True)
class CoinDcxFuturesCandleRow:
    open_at: datetime
    close_at: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    provenance: HistoricalProvenance
    pair: str = BTC_USDT_PAIR

    def validated(self) -> "CoinDcxFuturesCandleRow":
        self.provenance.validated()
        if _utc(self.close_at) <= _utc(self.open_at):
            raise ValueError("close_at must be after open_at")
        if _utc(self.available_at) < _utc(self.close_at):
            raise ValueError("futures candle cannot be visible before completion")
        values = {name: float(getattr(self, name)) for name in ("open", "high", "low", "close", "volume")}
        if any(not isfinite(value) for value in values.values()):
            raise ValueError("futures candle values must be finite")
        if min(values["open"], values["high"], values["low"], values["close"]) <= 0 or values["volume"] < 0:
            raise ValueError("invalid futures candle values")
        if values["high"] < max(values["open"], values["close"], values["low"]):
            raise ValueError("invalid futures candle high")
        if values["low"] > min(values["open"], values["close"], values["high"]):
            raise ValueError("invalid futures candle low")
        return self


@dataclass(frozen=True)
class CoinDcxFuturesRtCapture:
    first_seen_at: datetime
    provider_snapshot_at: datetime | None
    provider_tick_at: datetime | None
    mark_price_at: datetime | None
    funding_rate: float | None
    estimated_funding_rate: float | None
    mark_price: float | None
    last_price: float | None
    price_change_pct_24h: float | None
    volume_24h: float | None
    market: str | None
    raw_pair: str
    provenance: HistoricalProvenance

    def validated(self) -> "CoinDcxFuturesRtCapture":
        self.provenance.validated()
        if self.provenance.point_in_time_proven is not True:
            raise ValueError("RT capture must be point-in-time proven")
        if self.provenance.availability_basis != "FIRST_SEEN_CAPTURE":
            raise ValueError("RT capture must use FIRST_SEEN_CAPTURE")
        for name in (
            "funding_rate", "estimated_funding_rate", "mark_price", "last_price",
            "price_change_pct_24h", "volume_24h",
        ):
            value = getattr(self, name)
            if value is not None and not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.mark_price is not None and self.mark_price <= 0:
            raise ValueError("mark_price must be > 0")
        if self.last_price is not None and self.last_price <= 0:
            raise ValueError("last_price must be > 0")
        if self.volume_24h is not None and self.volume_24h < 0:
            raise ValueError("volume_24h must be >= 0")
        return self

    def context_evidence(self) -> Evidence:
        """Funding/mark snapshot alone is context, never a directional vote."""
        self.validated()
        return Evidence(
            family="DERIVATIVES_POSITIONING",
            causal_origin="LEVERAGED_POSITIONING",
            stance="UNKNOWN",
            strength="LOW",
            confidence=0.7,
            observed_at=_utc(self.first_seen_at),
            reason="CoinDCX futures funding/mark snapshot captured point-in-time; OI/liquidation confirmation is absent, so it remains context-only.",
            context_only=True,
            source="COINDCX_FUTURES_RT",
            metadata={
                **asdict(self),
                "open_interest_inferred": False,
                "liquidations_inferred": False,
                "may_generate_futures_trade": False,
            },
        )


@dataclass(frozen=True)
class CoinDcxBtcProviderPolicy:
    enabled: bool = False
    timeout_seconds: float = 10.0
    max_spot_limit: int = 1000

    def validated(self) -> "CoinDcxBtcProviderPolicy":
        if not isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        if not 1 <= int(self.max_spot_limit) <= 1000:
            raise ValueError("max_spot_limit must be between 1 and 1000")
        return self


def spot_candle_params(*, interval: str, start_at: datetime | None = None, end_at: datetime | None = None, limit: int = 1000) -> dict:
    if interval not in SPOT_INTERVALS:
        raise ValueError("unsupported CoinDCX spot interval")
    if not 1 <= int(limit) <= 1000:
        raise ValueError("CoinDCX spot candle limit must be 1..1000")
    params: dict[str, Any] = {"pair": BTC_USDT_PAIR, "interval": interval, "limit": int(limit)}
    if start_at is not None:
        params["startTime"] = int(_utc(start_at).timestamp() * 1000)
    if end_at is not None:
        params["endTime"] = int(_utc(end_at).timestamp() * 1000)
    if start_at is not None and end_at is not None and _utc(end_at) <= _utc(start_at):
        raise ValueError("end_at must be after start_at")
    return params


def futures_candle_params(*, resolution: str, start_at: datetime, end_at: datetime) -> dict:
    if resolution not in FUTURES_RESOLUTIONS:
        raise ValueError("unsupported CoinDCX futures resolution")
    start, end = _utc(start_at), _utc(end_at)
    if end <= start:
        raise ValueError("end_at must be after start_at")
    return {
        "pair": BTC_USDT_PAIR,
        "from": int(start.timestamp()),
        "to": int(end.timestamp()),
        "resolution": resolution,
        "pcode": "f",
    }


def normalize_coindcx_futures_candles(payload: dict, *, resolution: str) -> list[CoinDcxFuturesCandleRow]:
    if resolution not in FUTURES_RESOLUTIONS:
        raise ValueError("unsupported CoinDCX futures resolution")
    if not isinstance(payload, dict) or payload.get("s") != "ok" or not isinstance(payload.get("data"), list):
        raise ValueError("invalid CoinDCX futures candle payload")
    duration = timedelta(seconds=FUTURES_RESOLUTIONS[resolution])
    rows: dict[datetime, CoinDcxFuturesCandleRow] = {}
    for raw in payload["data"]:
        open_at = datetime.fromtimestamp(float(raw["time"]) / 1000.0, tz=timezone.utc)
        close_at = open_at + duration
        row = CoinDcxFuturesCandleRow(
            open_at=open_at,
            close_at=close_at,
            available_at=close_at,
            open=float(raw["open"]), high=float(raw["high"]), low=float(raw["low"]),
            close=float(raw["close"]), volume=float(raw.get("volume", 0.0)),
            provenance=HistoricalProvenance(
                provider="COINDCX",
                source_id=f"btc-usdt-futures:{resolution}:{int(float(raw['time']))}",
                availability_basis="BAR_COMPLETION_RECONSTRUCTION",
                point_in_time_proven=True,
                reconstructible_public_data=True,
            ),
        ).validated()
        if open_at in rows and rows[open_at] != row:
            raise ValueError("conflicting duplicate futures candle")
        rows[open_at] = row
    return sorted(rows.values(), key=lambda row: _utc(row.open_at))


def normalize_coindcx_futures_rt(payload: dict, *, first_seen_at: datetime) -> CoinDcxFuturesRtCapture:
    if not isinstance(payload, dict) or not isinstance(payload.get("prices"), dict):
        raise ValueError("invalid CoinDCX futures RT payload")
    prices = payload["prices"]
    raw = prices.get(BTC_USDT_PAIR)
    if not isinstance(raw, dict):
        raise ValueError("BTC_USDT futures snapshot missing")
    first_seen = _utc(first_seen_at)
    provider_snapshot = _ms(payload.get("ts"))
    provider_tick = _ms(raw.get("btST") or raw.get("ctRT"))
    mark_at = _ms(raw.get("bmST") or raw.get("cmRT"))
    return CoinDcxFuturesRtCapture(
        first_seen_at=first_seen,
        provider_snapshot_at=provider_snapshot,
        provider_tick_at=provider_tick,
        mark_price_at=mark_at,
        funding_rate=_finite_optional(raw.get("fr")),
        estimated_funding_rate=_finite_optional(raw.get("efr")),
        mark_price=_finite_optional(raw.get("mp")),
        last_price=_finite_optional(raw.get("ls")),
        price_change_pct_24h=_finite_optional(raw.get("pc")),
        volume_24h=_finite_optional(raw.get("v")),
        market=None if raw.get("mkt") is None else str(raw.get("mkt")),
        raw_pair=BTC_USDT_PAIR,
        provenance=HistoricalProvenance(
            provider="COINDCX",
            source_id=f"btc-futures-rt:{int(first_seen.timestamp() * 1000)}",
            availability_basis="FIRST_SEEN_CAPTURE",
            point_in_time_proven=True,
            immutable_archive=True,
            reconstructible_public_data=False,
        ),
    ).validated()


class CoinDcxBtcPublicProvider:
    def __init__(self, policy: CoinDcxBtcProviderPolicy | None = None, client: httpx.Client | None = None):
        self.policy = (policy or CoinDcxBtcProviderPolicy()).validated()
        self._client = client

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("CoinDCX BTC collection is disabled by policy")

    def _get_json(self, url: str, *, params: dict | None = None) -> Any:
        self._require_enabled()
        if self._client is not None:
            response = self._client.get(url, params=params, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def fetch_spot_candles(
        self, *, interval: str, start_at: datetime | None = None,
        end_at: datetime | None = None, limit: int = 1000,
    ) -> list[BtcSpotCandleArchiveRow]:
        payload = self._get_json(
            SPOT_CANDLES_URL,
            params=spot_candle_params(interval=interval, start_at=start_at, end_at=end_at, limit=limit),
        )
        return normalize_coindcx_spot_candles(payload, interval=interval)

    def fetch_futures_candles(
        self, *, resolution: str, start_at: datetime, end_at: datetime,
    ) -> list[CoinDcxFuturesCandleRow]:
        payload = self._get_json(
            FUTURES_CANDLES_URL,
            params=futures_candle_params(resolution=resolution, start_at=start_at, end_at=end_at),
        )
        return normalize_coindcx_futures_candles(payload, resolution=resolution)

    def capture_futures_rt(self, *, first_seen_at: datetime) -> CoinDcxFuturesRtCapture:
        payload = self._get_json(FUTURES_CURRENT_PRICES_URL)
        return normalize_coindcx_futures_rt(payload, first_seen_at=first_seen_at)


def architecture_contract() -> dict:
    return {
        "version": "COINDCX_BTC_PUBLIC_PROVIDER_CONTRACT_V1",
        "default_pair": BTC_USDT_PAIR,
        "collection_enabled_by_default": False,
        "network_call_at_import": False,
        "spot_history_supported": True,
        "futures_candle_history_supported": True,
        "current_futures_funding_capture_supported": True,
        "current_futures_snapshot_may_be_backdated": False,
        "open_interest_inferred_from_volume": False,
        "liquidations_inferred_from_price": False,
        "historical_options_api_claimed": False,
        "options_execution_enabled": False,
        "futures_execution_enabled": False,
        "research_only": True,
    }
