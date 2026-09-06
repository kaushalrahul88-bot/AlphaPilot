"""Documented Delta India BTC perpetual positioning context.

Two public, read-only Delta sources are supported:
- historical OI candles via ``/v2/history/candles?symbol=OI:BTCUSD``;
- one-shot live BTCUSD ticker snapshots from REST + the public WebSocket ticker.

The WebSocket ticker publishes provider-defined 6-hour OI value change. Combining
that with the current REST ``oi_value_usd`` lets AlphaPilot compute a provider
rolling OI percentage without inventing historical observations. All network
observations are captured before the decision clock is assigned by the caller.

Both paths are leveraged-positioning context for the shared BTC thesis only.
They never create a Futures trade and can never substitute for an Options quote
or fill. OI contraction remains UNKNOWN without liquidation-side evidence.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from math import isfinite
from typing import Any, Callable, Iterable

import httpx

from app.crypto_market_intelligence import Evidence, derivatives_context

DELTA_INDIA_HISTORY_CANDLES_URL = "https://api.india.delta.exchange/v2/history/candles"
DELTA_INDIA_BTC_TICKER_URL = "https://api.india.delta.exchange/v2/tickers/BTCUSD"
DELTA_INDIA_PUBLIC_WS_URL = "wss://public-socket.india.delta.exchange"
DELTA_BTC_PERPETUAL_SYMBOL = "BTCUSD"
DELTA_BTC_OI_SYMBOL = f"OI:{DELTA_BTC_PERPETUAL_SYMBOL}"
RESOLUTION_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "1d": 86400,
    "1w": 604800,
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _epoch(value: Any) -> datetime:
    number = float(value)
    if not isfinite(number) or number <= 0:
        raise ValueError("Delta timestamp must be finite and > 0")
    if number >= 1e15:
        number /= 1_000_000.0
    elif number >= 1e12:
        number /= 1_000.0
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _number(name: str, value: Any, *, positive: bool = False, nonnegative: bool = False) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{name} must be > 0")
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be >= 0")
    return number


def _provider_at(value: Any, *, received_at: datetime) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        stamp = _epoch(value)
    except (TypeError, ValueError):
        return None
    return stamp if stamp <= _utc(received_at) else None


@dataclass(frozen=True)
class DeltaIndiaBtcOiCandle:
    open_at: datetime
    available_at: datetime
    resolution: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def validated(self) -> "DeltaIndiaBtcOiCandle":
        if self.resolution not in RESOLUTION_SECONDS:
            raise ValueError("unsupported Delta OI candle resolution")
        open_at = _utc(self.open_at)
        available_at = _utc(self.available_at)
        expected = open_at + timedelta(seconds=RESOLUTION_SECONDS[self.resolution])
        if available_at != expected:
            raise ValueError("Delta OI available_at must equal open_at + resolution")
        for name in ("open", "high", "low", "close"):
            _number(name, getattr(self, name), positive=True)
        _number("volume", self.volume, nonnegative=True)
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Delta OI high is inconsistent")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Delta OI low is inconsistent")
        return self

    def frozen_dict(self) -> dict:
        self.validated()
        return {
            "open_at": _utc(self.open_at).isoformat(),
            "available_at": _utc(self.available_at).isoformat(),
            "resolution": self.resolution,
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": float(self.volume),
            "symbol": DELTA_BTC_OI_SYMBOL,
        }


@dataclass(frozen=True)
class DeltaIndiaBtcLivePositioningSnapshot:
    first_seen_at: datetime
    rest_received_at: datetime
    websocket_received_at: datetime
    rest_provider_at: datetime | None
    websocket_provider_at: datetime | None
    current_oi_contracts: float
    current_oi_value_usd: float
    oi_change_usd_6h: float
    oi_change_pct_6h: float
    spot_price: float

    def validated(self) -> "DeltaIndiaBtcLivePositioningSnapshot":
        first_seen = _utc(self.first_seen_at)
        rest_seen = _utc(self.rest_received_at)
        ws_seen = _utc(self.websocket_received_at)
        if first_seen != max(rest_seen, ws_seen):
            raise ValueError("first_seen_at must be the later of REST and WebSocket receive times")
        for provider in (self.rest_provider_at, self.websocket_provider_at):
            if provider is not None and _utc(provider) > first_seen:
                raise ValueError("provider timestamp cannot be after first_seen_at")
        _number("current_oi_contracts", self.current_oi_contracts, nonnegative=True)
        current_usd = _number("current_oi_value_usd", self.current_oi_value_usd, positive=True)
        change_usd = _number("oi_change_usd_6h", self.oi_change_usd_6h)
        change_pct = _number("oi_change_pct_6h", self.oi_change_pct_6h)
        _number("spot_price", self.spot_price, positive=True)
        previous_usd = current_usd - change_usd
        if previous_usd <= 0:
            raise ValueError("provider OI change implies non-positive 6h OI baseline")
        expected_pct = change_usd / previous_usd * 100.0
        if abs(expected_pct - change_pct) > 1e-9:
            raise ValueError("oi_change_pct_6h does not match provider OI values")
        return self

    def frozen_dict(self) -> dict:
        self.validated()
        return {
            "venue": "DELTA_EXCHANGE_INDIA",
            "symbol": DELTA_BTC_PERPETUAL_SYMBOL,
            "first_seen_at": _utc(self.first_seen_at).isoformat(),
            "rest_received_at": _utc(self.rest_received_at).isoformat(),
            "websocket_received_at": _utc(self.websocket_received_at).isoformat(),
            "rest_provider_at": None if self.rest_provider_at is None else _utc(self.rest_provider_at).isoformat(),
            "websocket_provider_at": None if self.websocket_provider_at is None else _utc(self.websocket_provider_at).isoformat(),
            "current_oi_contracts": float(self.current_oi_contracts),
            "current_oi_value_usd": float(self.current_oi_value_usd),
            "oi_change_usd_6h": float(self.oi_change_usd_6h),
            "oi_change_pct_6h": float(self.oi_change_pct_6h),
            "spot_price": float(self.spot_price),
            "rolling_window_hours": 6,
            "public_market_data_only": True,
            "authentication_used": False,
            "futures_context_only": True,
        }


@dataclass(frozen=True)
class DeltaIndiaBtcDerivativesContextPolicy:
    enabled: bool = False
    timeout_seconds: float = 10.0
    resolution: str = "5m"

    def validated(self) -> "DeltaIndiaBtcDerivativesContextPolicy":
        if self.resolution not in RESOLUTION_SECONDS:
            raise ValueError("unsupported Delta derivatives resolution")
        if not isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        return self


@dataclass(frozen=True)
class DeltaOiPositioningEvidencePolicy:
    lookback_hours: float = 1.0
    max_latest_age_seconds: int = 900
    min_abs_price_change_pct: float = 0.10
    min_oi_increase_pct: float = 0.10

    def validated(self) -> "DeltaOiPositioningEvidencePolicy":
        if not isfinite(float(self.lookback_hours)) or float(self.lookback_hours) <= 0:
            raise ValueError("lookback_hours must be finite and > 0")
        if int(self.max_latest_age_seconds) < 0:
            raise ValueError("max_latest_age_seconds must be >= 0")
        for name in ("min_abs_price_change_pct", "min_oi_increase_pct"):
            value = float(getattr(self, name))
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0")
        return self


@dataclass(frozen=True)
class DeltaLivePositioningEvidencePolicy:
    max_snapshot_age_seconds: int = 120
    min_abs_price_change_pct_6h: float = 0.25
    min_oi_increase_pct_6h: float = 0.10

    def validated(self) -> "DeltaLivePositioningEvidencePolicy":
        if int(self.max_snapshot_age_seconds) < 0:
            raise ValueError("max_snapshot_age_seconds must be >= 0")
        for name in ("min_abs_price_change_pct_6h", "min_oi_increase_pct_6h"):
            value = float(getattr(self, name))
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0")
        return self


def normalize_delta_btc_oi_candles(payload: dict[str, Any], *, resolution: str) -> list[DeltaIndiaBtcOiCandle]:
    if resolution not in RESOLUTION_SECONDS:
        raise ValueError("unsupported Delta OI candle resolution")
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("result"), list):
        raise ValueError("invalid Delta India historical OI payload")
    interval = RESOLUTION_SECONDS[resolution]
    rows: dict[datetime, DeltaIndiaBtcOiCandle] = {}
    for raw in payload["result"]:
        if not isinstance(raw, dict):
            continue
        try:
            open_at = _epoch(raw.get("time"))
            row = DeltaIndiaBtcOiCandle(
                open_at=open_at,
                available_at=open_at + timedelta(seconds=interval),
                resolution=resolution,
                open=_number("open", raw.get("open"), positive=True),
                high=_number("high", raw.get("high"), positive=True),
                low=_number("low", raw.get("low"), positive=True),
                close=_number("close", raw.get("close"), positive=True),
                volume=_number("volume", raw.get("volume", 0), nonnegative=True),
            ).validated()
        except (TypeError, ValueError):
            continue
        rows[_utc(open_at)] = row
    return [rows[key] for key in sorted(rows)]


def normalize_delta_btc_rest_ticker(payload: dict[str, Any], *, received_at: datetime) -> dict:
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("result"), dict):
        raise ValueError("invalid Delta India BTCUSD REST ticker payload")
    raw = payload["result"]
    if str(raw.get("symbol") or "") != DELTA_BTC_PERPETUAL_SYMBOL:
        raise ValueError("Delta REST ticker is not BTCUSD")
    return {
        "received_at": _utc(received_at),
        "provider_at": _provider_at(raw.get("timestamp"), received_at=received_at),
        "current_oi_contracts": _number("oi", raw.get("oi"), nonnegative=True),
        "current_oi_value_usd": _number("oi_value_usd", raw.get("oi_value_usd"), positive=True),
        "spot_price": _number("spot_price", raw.get("spot_price"), positive=True),
    }


def normalize_delta_btc_ws_ticker(payload: dict[str, Any], *, received_at: datetime) -> dict:
    if not isinstance(payload, dict) or payload.get("type") != "ticker":
        raise ValueError("Delta WebSocket payload is not ticker data")
    rows = payload.get("d")
    if not isinstance(rows, list):
        raise ValueError("Delta WebSocket ticker has no data rows")
    raw = next((row for row in rows if isinstance(row, dict) and str(row.get("s") or "") == DELTA_BTC_PERPETUAL_SYMBOL), None)
    if raw is None:
        raise ValueError("Delta WebSocket ticker has no BTCUSD row")
    oi = raw.get("oi")
    if not isinstance(oi, (list, tuple)) or len(oi) < 2:
        raise ValueError("Delta WebSocket BTCUSD ticker has no 6h OI change")
    spot = payload.get("sp")
    if spot in (None, ""):
        raise ValueError("Delta WebSocket BTCUSD ticker has no spot price")
    return {
        "received_at": _utc(received_at),
        "provider_at": _provider_at(payload.get("ts"), received_at=received_at),
        "current_oi_contracts": _number("oi_contracts", oi[0], nonnegative=True),
        "oi_change_usd_6h": _number("oi_change_usd_6h", oi[1]),
        "spot_price": _number("spot_price", spot, positive=True),
    }


def combine_delta_btc_live_positioning(rest: dict, websocket: dict) -> DeltaIndiaBtcLivePositioningSnapshot:
    rest_seen = _utc(rest["received_at"])
    ws_seen = _utc(websocket["received_at"])
    current_usd = _number("current_oi_value_usd", rest["current_oi_value_usd"], positive=True)
    change_usd = _number("oi_change_usd_6h", websocket["oi_change_usd_6h"])
    previous_usd = current_usd - change_usd
    if previous_usd <= 0:
        raise ValueError("Delta OI change implies non-positive 6h baseline")
    rest_contracts = _number("rest current_oi_contracts", rest["current_oi_contracts"], nonnegative=True)
    ws_contracts = _number("websocket current_oi_contracts", websocket["current_oi_contracts"], nonnegative=True)
    if max(rest_contracts, ws_contracts, 1.0) > 0:
        relative_gap = abs(rest_contracts - ws_contracts) / max(rest_contracts, ws_contracts, 1.0)
        if relative_gap > 0.05:
            raise ValueError("Delta REST/WebSocket BTCUSD OI snapshots disagree by more than 5%")
    return DeltaIndiaBtcLivePositioningSnapshot(
        first_seen_at=max(rest_seen, ws_seen),
        rest_received_at=rest_seen,
        websocket_received_at=ws_seen,
        rest_provider_at=rest.get("provider_at"),
        websocket_provider_at=websocket.get("provider_at"),
        current_oi_contracts=ws_contracts,
        current_oi_value_usd=current_usd,
        oi_change_usd_6h=change_usd,
        oi_change_pct_6h=change_usd / previous_usd * 100.0,
        spot_price=float(websocket["spot_price"]),
    ).validated()


class DeltaIndiaBtcDerivativesPublicProvider:
    def __init__(
        self,
        policy: DeltaIndiaBtcDerivativesContextPolicy | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.policy = (policy or DeltaIndiaBtcDerivativesContextPolicy()).validated()
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("Delta India BTC derivatives context provider is disabled")

    def _get_json(self, url: str, *, params: dict | None = None) -> Any:
        self._require_enabled()
        kwargs = {
            "params": params,
            "timeout": self.policy.timeout_seconds,
            "headers": {"Accept": "application/json"},
        }
        if self._client is not None:
            response = self._client.get(url, **kwargs)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(url, params=params, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()

    def fetch_oi_candles(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        resolution: str | None = None,
    ) -> list[DeltaIndiaBtcOiCandle]:
        selected_resolution = str(resolution or self.policy.resolution)
        if selected_resolution not in RESOLUTION_SECONDS:
            raise ValueError("unsupported Delta OI candle resolution")
        start = _utc(start_at)
        end = _utc(end_at)
        if end <= start:
            raise ValueError("end_at must be after start_at")
        payload = self._get_json(
            DELTA_INDIA_HISTORY_CANDLES_URL,
            params={
                "resolution": selected_resolution,
                "symbol": DELTA_BTC_OI_SYMBOL,
                "start": int(start.timestamp()),
                "end": int(end.timestamp()),
            },
        )
        rows = normalize_delta_btc_oi_candles(payload, resolution=selected_resolution)
        return [row for row in rows if _utc(row.open_at) >= start and _utc(row.open_at) <= end]

    def fetch_current_rest_ticker(self) -> dict:
        payload = self._get_json(DELTA_INDIA_BTC_TICKER_URL)
        received_at = _utc(self._clock())
        return normalize_delta_btc_rest_ticker(payload, received_at=received_at)

    async def fetch_current_ws_ticker(self, *, websocket_connect=None) -> dict:
        self._require_enabled()
        if websocket_connect is None:
            import websockets
            websocket_connect = websockets.connect
        subscription = {
            "type": "subscribe",
            "payload": {"channels": [{"name": "ticker", "symbols": [DELTA_BTC_PERPETUAL_SYMBOL]}]},
        }
        async with websocket_connect(
            DELTA_INDIA_PUBLIC_WS_URL,
            open_timeout=float(self.policy.timeout_seconds),
            close_timeout=2.0,
        ) as socket:
            await socket.send(json.dumps(subscription, separators=(",", ":")))
            deadline = asyncio.get_running_loop().time() + float(self.policy.timeout_seconds)
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("Delta public WebSocket BTCUSD ticker timed out")
                raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
                payload = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
                try:
                    return normalize_delta_btc_ws_ticker(payload, received_at=_utc(self._clock()))
                except ValueError:
                    continue

    async def capture_live_positioning_snapshot(self, *, websocket_connect=None) -> DeltaIndiaBtcLivePositioningSnapshot:
        rest = await asyncio.to_thread(self.fetch_current_rest_ticker)
        websocket = await self.fetch_current_ws_ticker(websocket_connect=websocket_connect)
        return combine_delta_btc_live_positioning(rest, websocket)


def _unknown(decision_at: datetime, reason: str, metadata: dict | None = None, *, source: str = "DELTA_EXCHANGE_INDIA_OI_HISTORY") -> Evidence:
    return Evidence(
        family="DERIVATIVES_POSITIONING",
        causal_origin="LEVERAGED_POSITIONING",
        stance="UNKNOWN",
        strength="LOW",
        confidence=0.55,
        observed_at=_utc(decision_at),
        reason=reason,
        context_only=True,
        source=source,
        metadata={
            **(metadata or {}),
            "futures_context_only": True,
            "may_inform_options": True,
            "may_generate_futures_trade": False,
        },
    )


def derive_delta_oi_positioning_evidence(
    rows: Iterable[DeltaIndiaBtcOiCandle],
    *,
    decision_at: datetime,
    price_change_pct: float,
    policy: DeltaOiPositioningEvidencePolicy | None = None,
) -> Evidence:
    """Create one leveraged-positioning origin from completed Delta OI history."""
    cfg = (policy or DeltaOiPositioningEvidencePolicy()).validated()
    decision = _utc(decision_at)
    price_change = _number("price_change_pct", price_change_pct)
    visible = sorted(
        [row.validated() for row in rows if _utc(row.available_at) <= decision],
        key=lambda row: _utc(row.available_at),
    )
    if not visible:
        return _unknown(decision, "No completed Delta BTC OI candle was available by decision time.")
    latest = visible[-1]
    age = (decision - _utc(latest.available_at)).total_seconds()
    if age < 0 or age > int(cfg.max_latest_age_seconds):
        return _unknown(decision, "Latest completed Delta BTC OI candle is stale for the intraday thesis.", {
            "latest_available_at": _utc(latest.available_at).isoformat(), "latest_age_seconds": age,
        })
    target = decision - timedelta(hours=float(cfg.lookback_hours))
    anchors = [row for row in visible if _utc(row.available_at) <= target]
    if not anchors:
        return _unknown(decision, "Delta BTC OI history does not yet cover the frozen positioning lookback.")
    anchor = anchors[-1]
    anchor_oi = float(anchor.close)
    latest_oi = float(latest.close)
    if anchor_oi <= 0:
        return _unknown(decision, "Delta BTC OI lookback anchor is non-positive.")
    oi_change_pct = (latest_oi - anchor_oi) / anchor_oi * 100.0
    metadata = {
        "resolution": latest.resolution,
        "lookback_hours": float(cfg.lookback_hours),
        "anchor_available_at": _utc(anchor.available_at).isoformat(),
        "latest_available_at": _utc(latest.available_at).isoformat(),
        "anchor_oi": anchor_oi,
        "latest_oi": latest_oi,
        "oi_change_pct": oi_change_pct,
        "price_change_pct": price_change,
        "historical_reconstruction": True,
        "completion_rule": "CANDLE_TIME_PLUS_RESOLUTION",
    }
    if oi_change_pct < float(cfg.min_oi_increase_pct):
        return _unknown(decision, "Delta BTC OI did not expand enough to support a fresh leveraged-positioning direction.", metadata)
    if abs(price_change) < float(cfg.min_abs_price_change_pct):
        return _unknown(decision, "BTC price change is too small for Delta OI expansion to form a directional positioning state.", metadata)
    evidence = derivatives_context(
        observed_at=_utc(latest.available_at),
        price_change_pct=price_change,
        oi_change_pct=oi_change_pct,
        funding_percentile=None,
        short_liquidations_usd=0.0,
        long_liquidations_usd=0.0,
        source="DELTA_EXCHANGE_INDIA_OI_HISTORY",
    )
    merged = dict(evidence.metadata)
    merged.update(metadata)
    merged.update({"liquidation_side_inferred": False, "futures_context_only": True, "may_generate_futures_trade": False})
    return replace(evidence, metadata=merged)


def derive_delta_live_positioning_evidence(
    snapshot: DeltaIndiaBtcLivePositioningSnapshot,
    *,
    decision_at: datetime,
    price_change_pct_6h: float,
    policy: DeltaLivePositioningEvidencePolicy | None = None,
) -> Evidence:
    """Interpret Delta's rolling 6h OI change without inventing liquidation side."""
    cfg = (policy or DeltaLivePositioningEvidencePolicy()).validated()
    snap = snapshot.validated()
    decision = _utc(decision_at)
    age = (decision - _utc(snap.first_seen_at)).total_seconds()
    metadata = {
        **snap.frozen_dict(),
        "price_change_pct_6h": float(price_change_pct_6h),
        "provider_rolling_oi_window_hours": 6,
        "liquidation_side_inferred": False,
    }
    if age < 0:
        raise ValueError("Delta positioning snapshot was first seen after decision_at")
    if age > int(cfg.max_snapshot_age_seconds):
        return _unknown(decision, "Delta live positioning snapshot is stale.", metadata, source="DELTA_EXCHANGE_INDIA_LIVE_TICKER")
    price_change = _number("price_change_pct_6h", price_change_pct_6h)
    if snap.oi_change_pct_6h < float(cfg.min_oi_increase_pct_6h):
        return _unknown(
            decision,
            "Delta 6h OI did not expand enough; contraction/flat OI stays non-directional without liquidation-side evidence.",
            metadata,
            source="DELTA_EXCHANGE_INDIA_LIVE_TICKER",
        )
    if abs(price_change) < float(cfg.min_abs_price_change_pct_6h):
        return _unknown(decision, "BTC 6h price change is too small for Delta OI expansion to form a directional state.", metadata, source="DELTA_EXCHANGE_INDIA_LIVE_TICKER")
    evidence = derivatives_context(
        observed_at=_utc(snap.first_seen_at),
        price_change_pct=price_change,
        oi_change_pct=float(snap.oi_change_pct_6h),
        funding_percentile=None,
        short_liquidations_usd=0.0,
        long_liquidations_usd=0.0,
        source="DELTA_EXCHANGE_INDIA_LIVE_TICKER",
    )
    merged = dict(evidence.metadata)
    merged.update(metadata)
    merged.update({"futures_context_only": True, "may_generate_futures_trade": False})
    return replace(evidence, metadata=merged)


def architecture_contract() -> dict:
    return {
        "version": "DELTA_INDIA_BTC_DERIVATIVES_CONTEXT_V2",
        "venue": "DELTA_EXCHANGE_INDIA",
        "public_history_endpoint": DELTA_INDIA_HISTORY_CANDLES_URL,
        "public_rest_ticker_endpoint": DELTA_INDIA_BTC_TICKER_URL,
        "public_websocket_endpoint": DELTA_INDIA_PUBLIC_WS_URL,
        "oi_symbol": DELTA_BTC_OI_SYMBOL,
        "authentication_required": False,
        "account_data_accessed": False,
        "historical_oi_supported_by_documentation": True,
        "live_ticker_oi_change_window_hours": 6,
        "live_snapshot_first_seen_required": True,
        "incomplete_candle_may_be_directional": False,
        "oi_contraction_may_be_directional_without_liquidations": False,
        "futures_context_may_inform_options": True,
        "futures_trade_generation_allowed": False,
        "options_quote_substitution_allowed": False,
        "live_execution": False,
        "capital_committed": 0,
        "research_only": True,
    }
