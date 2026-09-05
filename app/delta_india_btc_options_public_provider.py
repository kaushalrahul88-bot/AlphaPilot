"""Read-only Delta Exchange India BTC Options market-data probe.

This module uses only the documented public Delta India REST ticker endpoint.
It performs no authentication, account access, order placement, or execution.
The feed is a venue-candidate probe until AlphaPilot cross-checks live values
against the Delta India UI and the user explicitly adopts Delta as the Crypto
Options reference/execution venue.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from math import isfinite
from statistics import median
import re
from typing import Any, Callable

import httpx

DELTA_INDIA_TICKERS_URL = "https://api.india.delta.exchange/v2/tickers"
BTC_UNDERLYING = "BTC"
OPTION_CONTRACT_TYPES = ("call_options", "put_options")
_SYMBOL_RE = re.compile(r"^(?P<side>[CP])-BTC-(?P<strike>\d+(?:\.\d+)?)-(?P<expiry>\d{6})$")


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite_optional(value: Any) -> float | None:
    if value is None or value == "":
        return None
    number = float(value)
    if not isfinite(number):
        raise ValueError("numeric field must be finite")
    return number


def _positive_optional(value: Any) -> float | None:
    number = _finite_optional(value)
    if number is not None and number <= 0:
        return None
    return number


def _nonnegative_optional(value: Any) -> float | None:
    number = _finite_optional(value)
    if number is not None and number < 0:
        raise ValueError("numeric field must be non-negative")
    return number


def _provider_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    number = float(value)
    if not isfinite(number) or number <= 0:
        return None
    # REST examples use seconds; tolerate millisecond/microsecond precision.
    if number >= 1e15:
        number /= 1_000_000.0
    elif number >= 1e12:
        number /= 1_000.0
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _parse_symbol(symbol: str) -> tuple[str, float, date]:
    match = _SYMBOL_RE.fullmatch(str(symbol or "").strip())
    if not match:
        raise ValueError(f"unsupported Delta BTC option symbol: {symbol!r}")
    side = "CALL" if match.group("side") == "C" else "PUT"
    strike = float(match.group("strike"))
    if not isfinite(strike) or strike <= 0:
        raise ValueError("option strike must be finite and > 0")
    expiry = datetime.strptime(match.group("expiry"), "%d%m%y").date()
    return side, strike, expiry


@dataclass(frozen=True)
class DeltaIndiaBtcOptionQuote:
    first_seen_at: datetime
    provider_at: datetime | None
    symbol: str
    product_id: int | None
    option_type: str
    expiry_date: date
    strike_price: float
    spot_price: float | None
    mark_price: float | None
    best_bid: float | None
    best_ask: float | None
    bid_size: float | None
    ask_size: float | None
    bid_iv: float | None
    ask_iv: float | None
    open_interest: float | None
    volume: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    rho: float | None
    contract_type: str

    def validated(self) -> "DeltaIndiaBtcOptionQuote":
        if self.option_type not in {"CALL", "PUT"}:
            raise ValueError("option_type must be CALL or PUT")
        if self.contract_type not in OPTION_CONTRACT_TYPES:
            raise ValueError("unsupported Delta option contract type")
        if self.strike_price <= 0 or not isfinite(self.strike_price):
            raise ValueError("strike_price must be finite and > 0")
        if self.provider_at is not None and _utc(self.provider_at) > _utc(self.first_seen_at):
            raise ValueError("provider timestamp cannot be after AlphaPilot first_seen_at")
        for name in ("spot_price", "mark_price", "best_bid", "best_ask"):
            value = getattr(self, name)
            if value is not None and (not isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and > 0 when present")
        for name in ("bid_size", "ask_size", "open_interest", "volume"):
            value = getattr(self, name)
            if value is not None and (not isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and >= 0 when present")
        for name in ("bid_iv", "ask_iv"):
            value = getattr(self, name)
            if value is not None and (not isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and >= 0 when present")
        for name in ("delta", "gamma", "theta", "vega", "rho"):
            value = getattr(self, name)
            if value is not None and not isfinite(value):
                raise ValueError(f"{name} must be finite when present")
        if self.best_bid is not None and self.best_ask is not None and self.best_ask < self.best_bid:
            raise ValueError("best_ask cannot be below best_bid")
        return self

    def frozen_dict(self) -> dict[str, Any]:
        self.validated()
        return {
            "symbol": self.symbol,
            "product_id": self.product_id,
            "option_type": self.option_type,
            "expiry_date": self.expiry_date.isoformat(),
            "strike_price": self.strike_price,
            "spot_price": self.spot_price,
            "mark_price": self.mark_price,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "bid_iv": self.bid_iv,
            "ask_iv": self.ask_iv,
            "open_interest": self.open_interest,
            "volume": self.volume,
            "greeks": {
                "delta": self.delta,
                "gamma": self.gamma,
                "theta": self.theta,
                "vega": self.vega,
                "rho": self.rho,
            },
            "provider_at": None if self.provider_at is None else _utc(self.provider_at).isoformat(),
        }


@dataclass(frozen=True)
class DeltaIndiaBtcOptionsSnapshot:
    first_seen_at: datetime
    nearest_expiry: date
    reference_spot_price: float
    full_chain_contract_count: int
    nearest_expiry_contract_count: int
    selected_strike_count: int
    quotes: tuple[DeltaIndiaBtcOptionQuote, ...]

    def validated(self) -> "DeltaIndiaBtcOptionsSnapshot":
        if self.reference_spot_price <= 0 or not isfinite(self.reference_spot_price):
            raise ValueError("reference_spot_price must be finite and > 0")
        if not self.quotes:
            raise ValueError("Delta BTC options snapshot must contain quotes")
        if self.full_chain_contract_count < len(self.quotes):
            raise ValueError("full_chain_contract_count cannot be below selected quote count")
        if self.nearest_expiry_contract_count < len(self.quotes):
            raise ValueError("nearest_expiry_contract_count cannot be below selected quote count")
        if self.selected_strike_count < 1:
            raise ValueError("selected_strike_count must be >= 1")
        for quote in self.quotes:
            quote.validated()
            if quote.expiry_date != self.nearest_expiry:
                raise ValueError("all selected quotes must belong to nearest_expiry")
        return self

    def frozen_dict(self) -> dict[str, Any]:
        self.validated()
        return {
            "version": "DELTA_INDIA_BTC_OPTIONS_LIVE_PROBE_V1",
            "venue": "DELTA_EXCHANGE_INDIA",
            "underlying": BTC_UNDERLYING,
            "candidate_only": True,
            "primary_trade_instrument": "OPTIONS",
            "execution_enabled": False,
            "trading_auth_used": False,
            "public_market_data_only": True,
            "first_seen_at": _utc(self.first_seen_at).isoformat(),
            "nearest_expiry": self.nearest_expiry.isoformat(),
            "reference_spot_price": self.reference_spot_price,
            "full_chain_contract_count": self.full_chain_contract_count,
            "nearest_expiry_contract_count": self.nearest_expiry_contract_count,
            "selected_strike_count": self.selected_strike_count,
            "quote_count": len(self.quotes),
            "quotes": [quote.frozen_dict() for quote in self.quotes],
        }


@dataclass(frozen=True)
class DeltaIndiaOptionsProbePolicy:
    enabled: bool = False
    timeout_seconds: float = 10.0
    atm_strikes: int = 7

    def validated(self) -> "DeltaIndiaOptionsProbePolicy":
        if not isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        if not 3 <= int(self.atm_strikes) <= 21:
            raise ValueError("atm_strikes must be between 3 and 21")
        return self


def delta_btc_options_params() -> dict[str, str]:
    return {
        "contract_types": "call_options,put_options",
        "underlying_asset_symbols": BTC_UNDERLYING,
    }


def normalize_delta_btc_options_snapshot(
    payload: dict[str, Any],
    *,
    first_seen_at: datetime,
    atm_strikes: int = 7,
) -> DeltaIndiaBtcOptionsSnapshot:
    if not 3 <= int(atm_strikes) <= 21:
        raise ValueError("atm_strikes must be between 3 and 21")
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("result"), list):
        raise ValueError("invalid Delta India tickers payload")

    seen_at = _utc(first_seen_at)
    parsed: list[DeltaIndiaBtcOptionQuote] = []
    for raw in payload["result"]:
        if not isinstance(raw, dict):
            continue
        contract_type = str(raw.get("contract_type") or "")
        if contract_type not in OPTION_CONTRACT_TYPES:
            continue
        symbol = str(raw.get("symbol") or "")
        try:
            side, strike, expiry = _parse_symbol(symbol)
        except ValueError:
            continue
        if expiry < seen_at.date():
            continue

        quotes = raw.get("quotes") if isinstance(raw.get("quotes"), dict) else {}
        greeks = raw.get("greeks") if isinstance(raw.get("greeks"), dict) else {}
        provider_at = _provider_timestamp(raw.get("timestamp"))
        # A provider timestamp a few milliseconds ahead of the local receive clock
        # is not accepted as PIT evidence; omit it rather than rewriting first_seen.
        if provider_at is not None and provider_at > seen_at:
            provider_at = None
        product_id = raw.get("product_id")
        if product_id is not None:
            product_id = int(product_id)

        parsed.append(
            DeltaIndiaBtcOptionQuote(
                first_seen_at=seen_at,
                provider_at=provider_at,
                symbol=symbol,
                product_id=product_id,
                option_type=side,
                expiry_date=expiry,
                strike_price=strike,
                spot_price=_positive_optional(raw.get("spot_price")),
                mark_price=_positive_optional(raw.get("mark_price")),
                best_bid=_positive_optional(quotes.get("best_bid")),
                best_ask=_positive_optional(quotes.get("best_ask")),
                bid_size=_nonnegative_optional(quotes.get("bid_size")),
                ask_size=_nonnegative_optional(quotes.get("ask_size")),
                bid_iv=_nonnegative_optional(quotes.get("bid_iv")),
                ask_iv=_nonnegative_optional(quotes.get("ask_iv")),
                open_interest=_nonnegative_optional(raw.get("oi")),
                volume=_nonnegative_optional(raw.get("volume")),
                delta=_finite_optional(greeks.get("delta")),
                gamma=_finite_optional(greeks.get("gamma")),
                theta=_finite_optional(greeks.get("theta")),
                vega=_finite_optional(greeks.get("vega")),
                rho=_finite_optional(greeks.get("rho")),
                contract_type=contract_type,
            ).validated()
        )

    if not parsed:
        raise ValueError("Delta India returned no live BTC option tickers")
    nearest_expiry = min(row.expiry_date for row in parsed)
    nearest = [row for row in parsed if row.expiry_date == nearest_expiry]
    spot_values = [row.spot_price for row in nearest if row.spot_price is not None]
    if not spot_values:
        raise ValueError("Delta India nearest BTC option expiry has no spot_price")
    reference_spot = float(median(spot_values))
    strikes = sorted({row.strike_price for row in nearest}, key=lambda value: (abs(value - reference_spot), value))
    selected_strikes = set(strikes[: int(atm_strikes)])
    selected = sorted(
        (row for row in nearest if row.strike_price in selected_strikes),
        key=lambda row: (row.strike_price, row.option_type),
    )
    if not selected:
        raise ValueError("Delta India ATM validation slice is empty")

    return DeltaIndiaBtcOptionsSnapshot(
        first_seen_at=seen_at,
        nearest_expiry=nearest_expiry,
        reference_spot_price=reference_spot,
        full_chain_contract_count=len(parsed),
        nearest_expiry_contract_count=len(nearest),
        selected_strike_count=len(selected_strikes),
        quotes=tuple(selected),
    ).validated()


class DeltaIndiaBtcOptionsPublicProvider:
    def __init__(
        self,
        policy: DeltaIndiaOptionsProbePolicy | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.policy = (policy or DeltaIndiaOptionsProbePolicy()).validated()
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("Delta India BTC Options probe is disabled by policy")

    def _get_json(self) -> Any:
        self._require_enabled()
        if self._client is not None:
            response = self._client.get(
                DELTA_INDIA_TICKERS_URL,
                params=delta_btc_options_params(),
                timeout=self.policy.timeout_seconds,
                headers={"Accept": "application/json"},
            )
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(
                    DELTA_INDIA_TICKERS_URL,
                    params=delta_btc_options_params(),
                    headers={"Accept": "application/json"},
                )
        response.raise_for_status()
        return response.json()

    def capture_btc_options_snapshot(self) -> DeltaIndiaBtcOptionsSnapshot:
        payload = self._get_json()
        received_at = _utc(self._clock())
        return normalize_delta_btc_options_snapshot(
            payload,
            first_seen_at=received_at,
            atm_strikes=self.policy.atm_strikes,
        )


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "DELTA_INDIA_BTC_OPTIONS_PUBLIC_PROVIDER_CONTRACT_V1",
        "venue": "DELTA_EXCHANGE_INDIA",
        "public_rest_endpoint": DELTA_INDIA_TICKERS_URL,
        "authentication_required": False,
        "account_data_accessed": False,
        "trading_permission_required": False,
        "order_placement_enabled": False,
        "live_execution_enabled": False,
        "candidate_only_until_ui_cross_check": True,
        "full_chain_fetched": True,
        "nearest_expiry_atm_slice_persisted": True,
        "historical_backfill_claimed": False,
        "first_seen_required": True,
        "research_only": True,
    }
