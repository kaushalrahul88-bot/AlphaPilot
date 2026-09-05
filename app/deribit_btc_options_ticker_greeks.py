"""Deribit BTC options ticker/Greeks point-in-time ingestion.

This module consumes documented Deribit option ticker notifications and derives
25-delta put/call volatility skew from *observed Black-Scholes deltas*. It does
not infer delta from strike or use Deribit contracts as CoinDCX execution data.

Transport is deliberately separate from semantics: a future WebSocket service
may feed notifications here, while tests and replay can use the exact same
normalization and point-in-time rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Iterable, Literal

OptionType = Literal["call", "put"]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _from_ms(value: Any, *, name: str) -> datetime:
    number = _finite(name, value, positive=True)
    return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)


def _finite(name: str, value: Any, *, nonnegative: bool = False, positive: bool = False) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be >= 0")
    if positive and number <= 0:
        raise ValueError(f"{name} must be > 0")
    return number


def _optional_finite(name: str, value: Any, *, nonnegative: bool = False) -> float | None:
    if value is None:
        return None
    return _finite(name, value, nonnegative=nonnegative)


@dataclass(frozen=True)
class DeribitOptionInstrumentMeta:
    instrument_name: str
    expiry_at: datetime
    strike: float
    option_type: OptionType

    def validated(self) -> "DeribitOptionInstrumentMeta":
        if not str(self.instrument_name or "").strip():
            raise ValueError("instrument_name is required")
        _finite("strike", self.strike, positive=True)
        if self.option_type not in {"call", "put"}:
            raise ValueError("option_type must be call or put")
        return self


def normalize_option_instruments(
    rows: Iterable[dict],
    *,
    as_of: datetime,
    min_seconds_to_expiry: int = 3600,
) -> dict[str, DeribitOptionInstrumentMeta]:
    """Normalize authoritative Deribit instrument metadata without name parsing."""
    cutoff = _utc(as_of) + timedelta(seconds=max(0, int(min_seconds_to_expiry)))
    normalized: dict[str, DeribitOptionInstrumentMeta] = {}
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("kind") != "option":
            continue
        if raw.get("is_active") is False or str(raw.get("state") or "open").lower() not in {"open", "locked"}:
            continue
        name = str(raw.get("instrument_name") or "").strip()
        option_type = str(raw.get("option_type") or "").lower()
        if not name or option_type not in {"call", "put"}:
            continue
        try:
            expiry = _from_ms(raw["expiration_timestamp"], name="expiration_timestamp")
            strike = _finite("strike", raw["strike"], positive=True)
        except (KeyError, TypeError, ValueError):
            continue
        if expiry <= cutoff:
            continue
        meta = DeribitOptionInstrumentMeta(
            instrument_name=name,
            expiry_at=expiry,
            strike=strike,
            option_type=option_type,
        ).validated()
        normalized[name] = meta
    if not normalized:
        raise ValueError("no active Deribit BTC option instruments remain after normalization")
    return normalized


def ticker_subscription_channels(instruments: Iterable[DeribitOptionInstrumentMeta], *, interval: str = "agg2") -> tuple[str, ...]:
    if interval not in {"100ms", "agg2"}:
        raise ValueError("public Deribit ticker interval must be 100ms or agg2")
    names = sorted({row.validated().instrument_name for row in instruments})
    if not names:
        raise ValueError("at least one option instrument is required for ticker subscriptions")
    return tuple(f"ticker.{name}.{interval}" for name in names)


@dataclass(frozen=True)
class DeribitOptionTickerGreeksCapture:
    instrument_name: str
    option_type: OptionType
    strike: float
    expiry_at: datetime
    provider_time: datetime
    first_seen_at: datetime
    underlying_price_usd: float
    mark_iv_pct: float
    bid_iv_pct: float | None
    ask_iv_pct: float | None
    open_interest_btc: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    provider: str = "DERIBIT_TICKER"

    def validated(self) -> "DeribitOptionTickerGreeksCapture":
        if not str(self.instrument_name or "").strip():
            raise ValueError("instrument_name is required")
        if self.option_type not in {"call", "put"}:
            raise ValueError("option_type must be call or put")
        _finite("strike", self.strike, positive=True)
        provider_time = _utc(self.provider_time)
        first_seen = _utc(self.first_seen_at)
        if provider_time > first_seen:
            raise ValueError("provider_time cannot be after AlphaPilot first_seen_at")
        if _utc(self.expiry_at) <= first_seen:
            raise ValueError("option expiry must be after first_seen_at")
        _finite("underlying_price_usd", self.underlying_price_usd, positive=True)
        _finite("mark_iv_pct", self.mark_iv_pct, positive=True)
        _optional_finite("bid_iv_pct", self.bid_iv_pct, nonnegative=True)
        _optional_finite("ask_iv_pct", self.ask_iv_pct, nonnegative=True)
        _finite("open_interest_btc", self.open_interest_btc, nonnegative=True)
        delta = _finite("delta", self.delta)
        if self.option_type == "call" and not (0.0 <= delta <= 1.0):
            raise ValueError("call delta must be between 0 and 1")
        if self.option_type == "put" and not (-1.0 <= delta <= 0.0):
            raise ValueError("put delta must be between -1 and 0")
        _finite("gamma", self.gamma, nonnegative=True)
        _finite("theta", self.theta)
        _finite("vega", self.vega, nonnegative=True)
        _finite("rho", self.rho)
        return self


def ticker_capture_from_notification(
    notification: dict,
    *,
    instruments: dict[str, DeribitOptionInstrumentMeta],
    first_seen_at: datetime,
) -> DeribitOptionTickerGreeksCapture:
    """Normalize one documented ``ticker.{instrument}.{interval}`` notification."""
    if not isinstance(notification, dict) or notification.get("method") != "subscription":
        raise ValueError("Deribit ticker ingestion requires a subscription notification")
    params = notification.get("params")
    if not isinstance(params, dict):
        raise ValueError("Deribit subscription params are missing")
    channel = str(params.get("channel") or "")
    data = params.get("data")
    if not channel.startswith("ticker.") or not isinstance(data, dict):
        raise ValueError("notification is not a Deribit ticker payload")

    name = str(data.get("instrument_name") or "").strip()
    if not name or name not in instruments:
        raise ValueError("ticker instrument is not present in the authoritative option metadata set")
    if f"ticker.{name}." not in channel:
        raise ValueError("ticker channel and payload instrument do not match")

    meta = instruments[name].validated()
    greeks = data.get("greeks")
    if not isinstance(greeks, dict):
        raise ValueError("option ticker lacks documented Greeks object")

    return DeribitOptionTickerGreeksCapture(
        instrument_name=name,
        option_type=meta.option_type,
        strike=float(meta.strike),
        expiry_at=_utc(meta.expiry_at),
        provider_time=_from_ms(data["timestamp"], name="ticker timestamp"),
        first_seen_at=_utc(first_seen_at),
        underlying_price_usd=_finite("underlying_price", data["underlying_price"], positive=True),
        mark_iv_pct=_finite("mark_iv", data["mark_iv"], positive=True),
        bid_iv_pct=_optional_finite("bid_iv", data.get("bid_iv"), nonnegative=True),
        ask_iv_pct=_optional_finite("ask_iv", data.get("ask_iv"), nonnegative=True),
        open_interest_btc=_finite("open_interest", data["open_interest"], nonnegative=True),
        delta=_finite("delta", greeks["delta"]),
        gamma=_finite("gamma", greeks["gamma"], nonnegative=True),
        theta=_finite("theta", greeks["theta"]),
        vega=_finite("vega", greeks["vega"], nonnegative=True),
        rho=_finite("rho", greeks["rho"]),
    ).validated()


@dataclass(frozen=True)
class DeribitDeltaSkewPolicy:
    target_abs_delta: float = 0.25
    max_delta_distance: float = 0.08
    max_ticker_age_seconds: int = 15
    max_pair_first_seen_gap_seconds: int = 5
    min_seconds_to_expiry: int = 3600
    max_expiries_to_scan: int = 4

    def validated(self) -> "DeribitDeltaSkewPolicy":
        target = _finite("target_abs_delta", self.target_abs_delta, positive=True)
        if not (0.0 < target < 1.0):
            raise ValueError("target_abs_delta must be between 0 and 1")
        distance = _finite("max_delta_distance", self.max_delta_distance, nonnegative=True)
        if distance >= 1.0:
            raise ValueError("max_delta_distance must be < 1")
        if int(self.max_ticker_age_seconds) <= 0:
            raise ValueError("max_ticker_age_seconds must be > 0")
        if int(self.max_pair_first_seen_gap_seconds) < 0:
            raise ValueError("max_pair_first_seen_gap_seconds must be >= 0")
        if int(self.min_seconds_to_expiry) < 0:
            raise ValueError("min_seconds_to_expiry must be >= 0")
        if int(self.max_expiries_to_scan) <= 0:
            raise ValueError("max_expiries_to_scan must be > 0")
        return self


@dataclass(frozen=True)
class DeribitBtcDeltaSkewSnapshot:
    first_seen_at: datetime
    provider_time: datetime
    expiry_at: datetime
    target_abs_delta: float
    call: DeribitOptionTickerGreeksCapture
    put: DeribitOptionTickerGreeksCapture
    call_delta_distance: float
    put_delta_distance: float
    put_call_skew_25d_iv_points: float
    provider: str = "DERIBIT_TICKER"

    def validated(self) -> "DeribitBtcDeltaSkewSnapshot":
        call = self.call.validated()
        put = self.put.validated()
        if call.option_type != "call" or put.option_type != "put":
            raise ValueError("25d skew snapshot requires one call and one put")
        if _utc(call.expiry_at) != _utc(put.expiry_at) or _utc(self.expiry_at) != _utc(call.expiry_at):
            raise ValueError("25d call/put must share one expiry")
        if _utc(self.provider_time) != max(_utc(call.provider_time), _utc(put.provider_time)):
            raise ValueError("snapshot provider_time must be the later contributing provider timestamp")
        if _utc(self.first_seen_at) != max(_utc(call.first_seen_at), _utc(put.first_seen_at)):
            raise ValueError("snapshot first_seen_at must be the later contributing AlphaPilot timestamp")
        target = _finite("target_abs_delta", self.target_abs_delta, positive=True)
        expected_call_distance = abs(float(call.delta) - target)
        expected_put_distance = abs(abs(float(put.delta)) - target)
        if abs(float(self.call_delta_distance) - expected_call_distance) > 1e-12:
            raise ValueError("call_delta_distance is inconsistent")
        if abs(float(self.put_delta_distance) - expected_put_distance) > 1e-12:
            raise ValueError("put_delta_distance is inconsistent")
        expected_skew = float(put.mark_iv_pct) - float(call.mark_iv_pct)
        if abs(float(self.put_call_skew_25d_iv_points) - expected_skew) > 1e-12:
            raise ValueError("25d skew must equal put mark IV minus call mark IV")
        return self


class DeribitBtcOptionsGreeksBook:
    """Latest-first-seen option ticker state with fail-closed 25-delta selection."""

    def __init__(self, policy: DeribitDeltaSkewPolicy | None = None):
        self.policy = (policy or DeribitDeltaSkewPolicy()).validated()
        self._latest: dict[str, DeribitOptionTickerGreeksCapture] = {}

    def ingest(self, capture: DeribitOptionTickerGreeksCapture) -> dict:
        row = capture.validated()
        current = self._latest.get(row.instrument_name)
        if current is not None:
            current_time = _utc(current.provider_time)
            candidate_time = _utc(row.provider_time)
            if candidate_time < current_time:
                return {"status": "STALE_PROVIDER_UPDATE_IGNORED", "instrument_name": row.instrument_name}
            if candidate_time == current_time:
                if row == current:
                    return {"status": "IDEMPOTENT_DUPLICATE", "instrument_name": row.instrument_name}
                raise ValueError("conflicting Deribit ticker content at identical provider timestamp")
        self._latest[row.instrument_name] = row
        return {"status": "TICKER_STATE_UPDATED", "instrument_name": row.instrument_name}

    def latest_count(self) -> int:
        return len(self._latest)

    def snapshot_25d(self, *, as_of: datetime) -> DeribitBtcDeltaSkewSnapshot | None:
        decision = _utc(as_of)
        policy = self.policy
        minimum_expiry = decision + timedelta(seconds=int(policy.min_seconds_to_expiry))
        visible: list[DeribitOptionTickerGreeksCapture] = []
        for row in self._latest.values():
            row.validated()
            seen = _utc(row.first_seen_at)
            age = (decision - seen).total_seconds()
            if seen > decision or age < 0 or age > int(policy.max_ticker_age_seconds):
                continue
            if _utc(row.expiry_at) <= minimum_expiry:
                continue
            visible.append(row)
        if not visible:
            return None

        by_expiry: dict[datetime, list[DeribitOptionTickerGreeksCapture]] = {}
        for row in visible:
            by_expiry.setdefault(_utc(row.expiry_at), []).append(row)

        for expiry in sorted(by_expiry)[: int(policy.max_expiries_to_scan)]:
            rows = by_expiry[expiry]
            calls = [row for row in rows if row.option_type == "call"]
            puts = [row for row in rows if row.option_type == "put"]
            if not calls or not puts:
                continue
            target = float(policy.target_abs_delta)
            call = min(calls, key=lambda row: abs(float(row.delta) - target))
            put = min(puts, key=lambda row: abs(abs(float(row.delta)) - target))
            call_distance = abs(float(call.delta) - target)
            put_distance = abs(abs(float(put.delta)) - target)
            if call_distance > float(policy.max_delta_distance) or put_distance > float(policy.max_delta_distance):
                continue
            seen_gap = abs((_utc(call.first_seen_at) - _utc(put.first_seen_at)).total_seconds())
            if seen_gap > int(policy.max_pair_first_seen_gap_seconds):
                continue
            return DeribitBtcDeltaSkewSnapshot(
                first_seen_at=max(_utc(call.first_seen_at), _utc(put.first_seen_at)),
                provider_time=max(_utc(call.provider_time), _utc(put.provider_time)),
                expiry_at=expiry,
                target_abs_delta=target,
                call=call,
                put=put,
                call_delta_distance=call_distance,
                put_delta_distance=put_distance,
                put_call_skew_25d_iv_points=float(put.mark_iv_pct) - float(call.mark_iv_pct),
            ).validated()
        return None


def architecture_contract() -> dict:
    return {
        "version": "DERIBIT_BTC_OPTIONS_TICKER_GREEKS_V1",
        "documented_ticker_channel": "ticker.{instrument_name}.{interval}",
        "ticker_interval_default_for_public_collection": "agg2",
        "greeks_source": "DERIBIT_OPTION_TICKER",
        "delta_is_observed_black_scholes_delta": True,
        "delta_inferred_from_strike": False,
        "gamma_captured": True,
        "theta_captured": True,
        "vega_captured": True,
        "rho_captured": True,
        "mark_iv_captured": True,
        "skew_definition": "PUT_25D_MARK_IV_MINUS_CALL_25D_MARK_IV",
        "missing_valid_25d_pair_returns_none": True,
        "future_or_stale_ticker_used": False,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "underlying_direction_generation_allowed": False,
        "options_trade_generation_allowed": False,
        "futures_trade_generation_allowed": False,
        "research_only": True,
    }
