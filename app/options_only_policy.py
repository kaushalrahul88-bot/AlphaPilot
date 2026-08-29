"""AlphaPilot options-only execution boundary.

Underlying futures/spot/reference data may be used for market context, strike selection,
moneyness and validation. They are never execution-eligible instruments in this policy.
"""
from __future__ import annotations

OPTION_TYPES = {"CE", "PE"}
FUTURE_MARKERS = ("FUT", "FUTURE", "FUTURES")
ALLOWED_OPTION_ACTIONS = {"BUY CE", "BUY PE"}


def options_only_policy() -> dict:
    return {
        "mode": "OPTIONS_ONLY_V1",
        "trade_instruments": ["OPTIONS"],
        "allowed_option_types": sorted(OPTION_TYPES),
        "allowed_actions": sorted(ALLOWED_OPTION_ACTIONS),
        "underlying_reference_allowed": True,
        "futures_execution_allowed": False,
        "spot_execution_allowed": False,
        "cash_execution_allowed": False,
        "production_rule": True,
        "guardrail": (
            "Underlying futures/spot prices may inform market context, strike selection, "
            "moneyness and risk diagnostics, but AlphaPilot must never emit or execute "
            "a futures/spot/cash trade from the commodity options workflow."
        ),
    }


def assert_option_contract(contract: dict) -> dict:
    if not isinstance(contract, dict):
        raise ValueError("Option contract must be a mapping")
    option_type = str(contract.get("option_type") or "").upper()
    trading_symbol = str(contract.get("trading_symbol") or "").upper()
    if option_type not in OPTION_TYPES:
        raise ValueError("Options-only policy rejected non-option contract")
    if any(trading_symbol.endswith(marker) for marker in FUTURE_MARKERS):
        raise ValueError("Options-only policy rejected futures contract")
    if not trading_symbol:
        raise ValueError("Option contract requires trading_symbol")
    return contract


def assert_option_action(action: str) -> str:
    normalized = " ".join(str(action or "").upper().split())
    if normalized not in ALLOWED_OPTION_ACTIONS:
        raise ValueError("Options-only policy rejected non-option action")
    return normalized


def mark_underlying_reference(payload: dict | None, instrument: str = "MCX_COPPER_FUTURE") -> dict:
    return {
        "instrument": instrument,
        "reference_only": True,
        "execution_eligible": False,
        "data": payload,
    }
