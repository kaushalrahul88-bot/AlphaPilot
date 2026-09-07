"""Run-scoped cash-symbol aliases for frozen historical research protocols.

The candle-only F&O replay deliberately keeps its frozen logical stock identity
(`LTIM`) so the experiment definition and deterministic click seed do not move.
Market-data lookup, however, must use the currently valid NSE symbol (`LTM`).
The alias is installed only on the concrete provider instance for the duration
of the replay and is restored afterward, preserving the provider class and its
existing authentication/rate-limit behavior.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


CURRENT_NSE_CASH_ALIASES = {
    "LTIM": ("LTM", "NSE-LTM"),
}


@contextmanager
def current_cash_symbol_aliases(provider: Any) -> Iterator[Any]:
    """Temporarily normalize renamed NSE cash symbols on the real provider."""
    had_instance_override = "_instrument" in getattr(provider, "__dict__", {})
    prior_instance_override = getattr(provider, "__dict__", {}).get("_instrument")
    original_instrument = provider._instrument

    def instrument(symbol: str):
        alias = CURRENT_NSE_CASH_ALIASES.get(symbol)
        if alias is None:
            return original_instrument(symbol)
        trading_symbol, groww_symbol = alias
        return ("NSE", "CASH", trading_symbol, groww_symbol)

    provider._instrument = instrument
    try:
        yield provider
    finally:
        if had_instance_override:
            provider._instrument = prior_instance_override
        else:
            delattr(provider, "_instrument")


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_CANDLE_ONLY_CURRENT_CASH_SYMBOL_ALIAS_V1",
        "scope": "CANDLE_ONLY_REPLAY_PROVIDER_INSTANCE",
        "aliases": {
            logical: {
                "exchange": "NSE",
                "segment": "CASH",
                "trading_symbol": values[0],
                "groww_symbol": values[1],
            }
            for logical, values in CURRENT_NSE_CASH_ALIASES.items()
        },
        "provider_class_changed": False,
        "strategy_identity_changed": False,
        "deterministic_click_seed_changed": False,
        "option_mapping_changed": False,
        "futures_mapping_changed": False,
    }
