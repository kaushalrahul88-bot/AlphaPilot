"""BTC Options-only translation preflight, research/shadow only.

Consumes an already-formed instrument-neutral BTC market state plus BTC options
context. It may identify BUY_CALL/BUY_PUT as the *route-side candidate* for the
later contract-selection module, but it does not select a strike/expiry, create
an order, use futures leverage, or generate a Futures trade.
"""
from __future__ import annotations

from app.crypto_market_intelligence import Evidence


def options_route_preflight(*, btc_market_state: dict, options_context: Evidence | None) -> dict:
    if str(btc_market_state.get("asset", "")).upper() != "BTC":
        raise ValueError("BTC options preflight requires a BTC market state")
    if btc_market_state.get("instrument_neutral") is not True:
        raise ValueError("BTC options preflight requires an instrument-neutral market state")
    if btc_market_state.get("futures_trade_generated") is True:
        raise ValueError("cannot enter Options preflight from a state containing a Futures trade")
    if btc_market_state.get("options_trade_generated") is True:
        raise ValueError("Options preflight requires a pre-trade market state")

    direction = str(btc_market_state.get("direction", "UNKNOWN")).upper()
    if direction not in {"BULLISH", "BEARISH"}:
        return {
            "version": "BTC_OPTIONS_PREFLIGHT_V1",
            "asset": "BTC",
            "instrument_type": "OPTIONS",
            "status": "NO_UNDERLYING_THESIS",
            "side_candidate": "NO_TRADE",
            "reason": "Shared BTC Market Brain has no confirmed directional thesis.",
            "contract_selection_allowed": False,
            "trade_generated": False,
            "futures_trade_generated": False,
            "broker_execution_enabled": False,
            "capital_committed": 0,
        }

    if options_context is None:
        return {
            "version": "BTC_OPTIONS_PREFLIGHT_V1",
            "asset": "BTC",
            "instrument_type": "OPTIONS",
            "status": "OPTIONS_CONTEXT_MISSING",
            "side_candidate": "BUY_CALL" if direction == "BULLISH" else "BUY_PUT",
            "reason": "Underlying thesis exists but BTC options IV/skew/OI context is unavailable; contract selection remains blocked.",
            "contract_selection_allowed": False,
            "trade_generated": False,
            "futures_trade_generated": False,
            "broker_execution_enabled": False,
            "capital_committed": 0,
        }

    if options_context.family != "BTC_OPTIONS_MARKET":
        raise ValueError("Options preflight requires BTC_OPTIONS_MARKET context")
    if options_context.context_only is not True:
        raise ValueError("BTC options-market evidence must remain context-only")
    if options_context.metadata.get("may_generate_futures_trade") is True:
        raise ValueError("Options preflight rejected Futures-generating context")

    tags = list(options_context.metadata.get("tags") or [])
    caution = []
    if "IV_EXTREME_HIGH" in tags:
        caution.append("IV_EXTREME_HIGH")
    if "PUT_SKEW_ELEVATED" in tags or "CALL_SKEW_ELEVATED" in tags:
        caution.append("SKEW_EXTREME")

    side = "BUY_CALL" if direction == "BULLISH" else "BUY_PUT"
    return {
        "version": "BTC_OPTIONS_PREFLIGHT_V1",
        "asset": "BTC",
        "instrument_type": "OPTIONS",
        "status": "READY_FOR_OPTIONS_CONTRACT_SELECTION",
        "side_candidate": side,
        "underlying_direction": direction,
        "options_context_source": options_context.source,
        "options_context_tags": tags,
        "cautions": caution,
        "contract_selection_allowed": True,
        "strike_selected": False,
        "expiry_selected": False,
        "premium_selected": False,
        "trade_generated": False,
        "futures_trade_generated": False,
        "futures_route_invoked": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_OPTIONS_PREFLIGHT_CONTRACT_V1",
        "input_market_state_must_be_instrument_neutral": True,
        "underlying_direction_created_by_options_chain": False,
        "options_context_required_before_contract_selection": True,
        "futures_route_invoked": False,
        "futures_leverage_allowed": False,
        "futures_funding_used_as_options_risk_model": False,
        "strike_or_expiry_selected_here": False,
        "trade_generated_here": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }
