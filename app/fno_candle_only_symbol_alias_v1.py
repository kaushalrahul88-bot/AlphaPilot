"""Point-in-time cash-symbol aliases for frozen historical research protocols.

The candle-only F&O replay deliberately keeps its frozen logical stock identity
(`LTIM`) so the experiment definition and deterministic click seed do not move.
Market-data lookup, however, must use the currently valid NSE symbol (`LTM`).
This adapter is intentionally scoped to the replay instead of silently changing
strategy inputs or option/futures generation elsewhere.
"""
from __future__ import annotations

from typing import Any


CURRENT_NSE_CASH_ALIASES = {
    "LTIM": ("LTM", "NSE-LTM"),
}


class CurrentCashSymbolAliasProvider:
    """Delegate provider access while normalizing renamed NSE cash symbols."""

    def __init__(self, delegate: Any):
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def _instrument(self, symbol: str):
        alias = CURRENT_NSE_CASH_ALIASES.get(symbol)
        if alias is None:
            return self._delegate._instrument(symbol)
        trading_symbol, groww_symbol = alias
        return ("NSE", "CASH", trading_symbol, groww_symbol)


def adapt_current_cash_symbols(provider: Any) -> CurrentCashSymbolAliasProvider:
    """Return the replay-scoped provider adapter."""
    return CurrentCashSymbolAliasProvider(provider)


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_CANDLE_ONLY_CURRENT_CASH_SYMBOL_ALIAS_V1",
        "aliases": {
            logical: {
                "exchange": "NSE",
                "segment": "CASH",
                "trading_symbol": values[0],
                "groww_symbol": values[1],
            }
            for logical, values in CURRENT_NSE_CASH_ALIASES.items()
        },
        "strategy_identity_changed": False,
        "deterministic_click_seed_changed": False,
        "option_mapping_changed": False,
        "futures_mapping_changed": False,
    }
