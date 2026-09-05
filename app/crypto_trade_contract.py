"""Hard separation contract for Crypto Options, Futures and Spot trade routes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

InstrumentType = Literal["OPTIONS", "FUTURES", "SPOT", "NO_TRADE"]


@dataclass(frozen=True)
class TradeIntent:
    instrument_type: InstrumentType
    asset: str
    action: str
    rationale: str
    metadata: dict | None = None


_ALLOWED_ACTIONS: dict[InstrumentType, set[str]] = {
    "OPTIONS": {"BUY_CALL", "BUY_PUT", "NO_TRADE"},
    "FUTURES": {"LONG", "SHORT", "NO_TRADE"},
    "SPOT": {"BUY", "SELL", "NO_TRADE"},
    "NO_TRADE": {"NO_TRADE"},
}

_FORBIDDEN_KEYS: dict[InstrumentType, set[str]] = {
    "OPTIONS": {"leverage", "liquidation_price", "funding_rate", "futures_contract", "futures_side"},
    "FUTURES": {"option_type", "strike", "expiry", "premium", "delta", "gamma", "theta", "vega"},
    "SPOT": {"leverage", "liquidation_price", "funding_rate", "option_type", "strike", "expiry", "premium"},
    "NO_TRADE": set(),
}


def validate_trade_intent(intent: TradeIntent) -> dict:
    instrument = intent.instrument_type
    action = str(intent.action or "").upper()
    if instrument not in _ALLOWED_ACTIONS:
        raise ValueError(f"unsupported instrument_type: {instrument}")
    if action not in _ALLOWED_ACTIONS[instrument]:
        raise ValueError(f"action {action} is not valid for {instrument}")

    metadata = dict(intent.metadata or {})
    forbidden = sorted(key for key in _FORBIDDEN_KEYS[instrument] if key in metadata and metadata[key] is not None)
    if forbidden:
        raise ValueError(f"{instrument} trade contains fields from another instrument route: {', '.join(forbidden)}")

    payload = asdict(intent)
    payload["action"] = action
    payload["route_guard_version"] = "CRYPTO_INSTRUMENT_SEPARATION_V1"
    payload["mixed_instrument_trade_allowed"] = False
    return payload


def options_trade_intent(*, asset: str, action: str, rationale: str, metadata: dict | None = None) -> dict:
    return validate_trade_intent(TradeIntent("OPTIONS", asset, action, rationale, metadata))


def futures_trade_intent(*, asset: str, action: str, rationale: str, metadata: dict | None = None) -> dict:
    return validate_trade_intent(TradeIntent("FUTURES", asset, action, rationale, metadata))


def no_trade_intent(*, asset: str, rationale: str) -> dict:
    return validate_trade_intent(TradeIntent("NO_TRADE", asset, "NO_TRADE", rationale, None))


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_INSTRUMENT_SEPARATION_V1",
        "default_platform": "COINDCX",
        "shared_market_context_allowed": True,
        "futures_data_may_inform_options_market_state": True,
        "options_data_may_inform_underlying_market_state": True,
        "options_and_futures_trade_generation_separate": True,
        "mixed_instrument_trade_allowed": False,
        "options_actions": sorted(_ALLOWED_ACTIONS["OPTIONS"]),
        "futures_actions": sorted(_ALLOWED_ACTIONS["FUTURES"]),
        "research_only": True,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }
