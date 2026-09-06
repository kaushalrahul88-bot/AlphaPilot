"""Documented Delta India BTC perpetual open-interest context.

The provider reads only Delta Exchange India's public historical candles endpoint
with ``symbol=OI:BTCUSD``. Historical OI candles are treated conservatively: a
candle is usable only after ``time + resolution``. The evidence layer uses OI
only as leveraged-positioning context for the shared BTC thesis; it never creates
a Futures trade and it cannot substitute for an Options quote or fill.

Research/shadow only. No authentication, account access, order placement, or
capital commitment.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Callable, Iterable

import httpx

from app.crypto_market_intelligence import Evidence, derivatives_context

DELTA_INDIA_HISTORY_CANDLES_URL = "https://api.india.delta.exchange/v2/history/candles"
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
        raise ValueError("Delta candle time must be finite and > 0")
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


class DeltaIndiaBtcDerivativesPublicProvider:
    def __init__(
        self,
        policy: DeltaIndiaBtcDerivativesContextPolicy | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.policy = (policy or DeltaIndiaBtcDerivativesContextPolicy()).validated()
        self._client = client

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("Delta India BTC derivatives context provider is disabled")

    def fetch_oi_candles(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        resolution: str | None = None,
    ) -> list[DeltaIndiaBtcOiCandle]:
        self._require_enabled()
        selected_resolution = str(resolution or self.policy.resolution)
        if selected_resolution not in RESOLUTION_SECONDS:
            raise ValueError("unsupported Delta OI candle resolution")
        start = _utc(start_at)
        end = _utc(end_at)
        if end <= start:
            raise ValueError("end_at must be after start_at")
        params = {
            "resolution": selected_resolution,
            "symbol": DELTA_BTC_OI_SYMBOL,
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
        }
        if self._client is not None:
            response = self._client.get(
                DELTA_INDIA_HISTORY_CANDLES_URL,
                params=params,
                timeout=self.policy.timeout_seconds,
                headers={"Accept": "application/json"},
            )
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(
                    DELTA_INDIA_HISTORY_CANDLES_URL,
                    params=params,
                    headers={"Accept": "application/json"},
                )
        response.raise_for_status()
        rows = normalize_delta_btc_oi_candles(response.json(), resolution=selected_resolution)
        return [row for row in rows if _utc(row.open_at) >= start and _utc(row.open_at) <= end]


def _unknown(decision_at: datetime, reason: str, metadata: dict | None = None) -> Evidence:
    return Evidence(
        family="DERIVATIVES_POSITIONING",
        causal_origin="LEVERAGED_POSITIONING",
        stance="UNKNOWN",
        strength="LOW",
        confidence=0.55,
        observed_at=_utc(decision_at),
        reason=reason,
        context_only=True,
        source="DELTA_EXCHANGE_INDIA_OI_HISTORY",
        metadata={
            **(metadata or {}),
            "symbol": DELTA_BTC_OI_SYMBOL,
            "historical_reconstruction": True,
            "completion_rule": "CANDLE_TIME_PLUS_RESOLUTION",
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
    """Create one leveraged-positioning origin from completed Delta OI history.

    OI expansion plus a material same-horizon price move may be directional.
    OI contraction is deliberately left UNKNOWN because it can represent either
    squeeze/deleveraging path without liquidation-side evidence.
    """
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
        return _unknown(
            decision,
            "Latest completed Delta BTC OI candle is stale for the intraday thesis.",
            {"latest_available_at": _utc(latest.available_at).isoformat(), "latest_age_seconds": age},
        )
    target = decision - timedelta(hours=float(cfg.lookback_hours))
    anchors = [row for row in visible if _utc(row.available_at) <= target]
    if not anchors:
        return _unknown(
            decision,
            "Delta BTC OI history does not yet cover the frozen positioning lookback.",
            {"latest_available_at": _utc(latest.available_at).isoformat()},
        )
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
        "min_abs_price_change_pct": float(cfg.min_abs_price_change_pct),
        "min_oi_increase_pct": float(cfg.min_oi_increase_pct),
    }
    if oi_change_pct < float(cfg.min_oi_increase_pct):
        return _unknown(
            decision,
            "Delta BTC OI did not expand enough to support a fresh leveraged-positioning direction.",
            metadata,
        )
    if abs(price_change) < float(cfg.min_abs_price_change_pct):
        return _unknown(
            decision,
            "BTC price change is too small for Delta OI expansion to form a directional positioning state.",
            metadata,
        )

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
    merged.update({
        "historical_reconstruction": True,
        "completion_rule": "CANDLE_TIME_PLUS_RESOLUTION",
        "liquidation_side_inferred": False,
        "futures_context_only": True,
        "may_generate_futures_trade": False,
    })
    return replace(evidence, metadata=merged)


def architecture_contract() -> dict:
    return {
        "version": "DELTA_INDIA_BTC_DERIVATIVES_CONTEXT_V1",
        "venue": "DELTA_EXCHANGE_INDIA",
        "public_history_endpoint": DELTA_INDIA_HISTORY_CANDLES_URL,
        "oi_symbol": DELTA_BTC_OI_SYMBOL,
        "authentication_required": False,
        "account_data_accessed": False,
        "historical_oi_supported": True,
        "candle_available_only_after_completion": True,
        "incomplete_candle_may_be_directional": False,
        "oi_contraction_may_be_directional_without_liquidations": False,
        "futures_context_may_inform_options": True,
        "futures_trade_generation_allowed": False,
        "options_quote_substitution_allowed": False,
        "live_execution": False,
        "capital_committed": 0,
        "research_only": True,
    }
