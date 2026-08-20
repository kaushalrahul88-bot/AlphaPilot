from .groww import GrowwProvider


class DynamicGrowwProvider(GrowwProvider):
    """Groww provider with dynamic NSE CASH symbol resolution.

    Groww documents stock/index groww_symbol as EXCHANGE-TRADING_SYMBOL,
    e.g. NSE-WIPRO. Keep the known mappings from the base provider, then
    safely construct the standard NSE CASH identifier for additional symbols.
    Option-chain calls continue to validate F&O availability independently.
    """

    def _instrument(self, symbol):
        symbol = symbol.upper().strip()

        try:
            return super()._instrument(symbol)
        except ValueError:
            if not symbol or not symbol.replace("&", "").replace("-", "").isalnum():
                raise ValueError(f"Invalid NSE symbol: {symbol!r}")

            return (
                "NSE",
                "CASH",
                symbol,
                f"NSE-{symbol}",
            )
