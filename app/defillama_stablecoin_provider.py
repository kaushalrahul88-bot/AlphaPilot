"""Optional DefiLlama stablecoin-liquidity provider for first-seen research capture.

The public endpoint is used only for aggregate USD-pegged stablecoin supply
state. Current supply is not treated as exchange buying flow or a directional
trade signal, and later historical API values are not backdated into click-time
knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any

import httpx

STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoins"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite_nonnegative(name: str, value: Any) -> float:
    number = float(value)
    if not isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and >= 0")
    return number


def _peg_amount(value: Any, peg_type: str) -> float | None:
    if isinstance(value, dict):
        raw = value.get(peg_type)
    else:
        raw = value
    if raw is None:
        return None
    try:
        return _finite_nonnegative("stablecoin circulating amount", raw)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class DefiLlamaStablecoinPolicy:
    enabled: bool = False
    timeout_seconds: float = 10.0
    peg_type: str = "peggedUSD"
    include_prices: bool = True

    def validated(self) -> "DefiLlamaStablecoinPolicy":
        timeout = float(self.timeout_seconds)
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("DefiLlama timeout_seconds must be finite and > 0")
        if not str(self.peg_type or "").strip():
            raise ValueError("DefiLlama peg_type is required")
        return self


@dataclass(frozen=True)
class DefiLlamaStablecoinSupplyCapture:
    first_seen_at: datetime
    peg_type: str
    total_circulating: float
    by_symbol: dict[str, float]
    prices: dict[str, float | None]
    asset_count: int
    provider: str = "DEFILLAMA_STABLECOINS"

    def validated(self) -> "DefiLlamaStablecoinSupplyCapture":
        _finite_nonnegative("total_circulating", self.total_circulating)
        if int(self.asset_count) < 0:
            raise ValueError("asset_count must be >= 0")
        total = 0.0
        for symbol, amount in self.by_symbol.items():
            if not str(symbol or "").strip():
                raise ValueError("stablecoin symbol cannot be empty")
            total += _finite_nonnegative(f"{symbol} circulating", amount)
        if abs(total - float(self.total_circulating)) > max(1.0, abs(float(self.total_circulating)) * 1e-9):
            raise ValueError("by_symbol supply does not reconcile to total_circulating")
        return self


class DefiLlamaStablecoinProvider:
    def __init__(self, policy: DefiLlamaStablecoinPolicy | None = None, client: httpx.Client | None = None):
        self.policy = (policy or DefiLlamaStablecoinPolicy()).validated()
        self._client = client

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("DefiLlama stablecoin collection is disabled by policy")

    def capture_supply(self, *, first_seen_at: datetime) -> DefiLlamaStablecoinSupplyCapture:
        self._require_enabled()
        params = {"includePrices": str(bool(self.policy.include_prices)).lower()}
        if self._client is not None:
            response = self._client.get(STABLECOINS_URL, params=params, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(STABLECOINS_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        assets = payload.get("peggedAssets") if isinstance(payload, dict) else None
        if not isinstance(assets, list):
            raise ValueError("invalid DefiLlama stablecoins response")

        by_symbol: dict[str, float] = {}
        prices: dict[str, float | None] = {}
        for raw in assets:
            if not isinstance(raw, dict) or str(raw.get("pegType") or "") != self.policy.peg_type:
                continue
            amount = _peg_amount(raw.get("circulating"), self.policy.peg_type)
            if amount is None:
                continue
            symbol = str(raw.get("symbol") or raw.get("name") or "UNKNOWN").strip().upper()
            if not symbol:
                continue
            by_symbol[symbol] = by_symbol.get(symbol, 0.0) + amount
            raw_price = raw.get("price")
            if raw_price is None:
                prices.setdefault(symbol, None)
            else:
                try:
                    price = float(raw_price)
                    prices[symbol] = price if isfinite(price) and price >= 0 else None
                except (TypeError, ValueError):
                    prices[symbol] = None

        total = sum(by_symbol.values())
        if not by_symbol:
            raise ValueError("DefiLlama response contains no usable USD-pegged stablecoin supply")
        return DefiLlamaStablecoinSupplyCapture(
            first_seen_at=_utc(first_seen_at),
            peg_type=self.policy.peg_type,
            total_circulating=total,
            by_symbol=dict(sorted(by_symbol.items())),
            prices={symbol: prices.get(symbol) for symbol in sorted(by_symbol)},
            asset_count=len(by_symbol),
        ).validated()


def architecture_contract() -> dict:
    return {
        "version": "DEFILLAMA_STABLECOIN_PROVIDER_V1",
        "collection_enabled_by_default": False,
        "api_key_required": False,
        "captures_current_usd_pegged_supply": True,
        "historical_values_backdated_to_click": False,
        "aggregate_supply_equals_exchange_inflow": False,
        "aggregate_supply_is_directional_trade_signal": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
