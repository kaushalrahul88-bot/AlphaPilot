from datetime import datetime, time
from zoneinfo import ZoneInfo

from .groww import GrowwProvider


class DynamicGrowwProvider(GrowwProvider):
    """Groww provider with dynamic NSE CASH symbol resolution."""

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

    def _market_session(self):
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        weekday_open = now.weekday() < 5
        session_open = time(9, 15) <= now.time() <= time(15, 30)
        is_open = weekday_open and session_open
        return {
            "timezone": "Asia/Kolkata",
            "checked_at": now.isoformat(),
            "is_open": is_open,
            "status": "OPEN" if is_open else "CLOSED",
            "regular_hours": "09:15-15:30 IST, Monday-Friday",
        }

    def _recommended_option(self, symbol, expiry, raw_chain, technical):
        if technical.get("status") != "SETUP":
            return None

        direction = technical.get("direction")
        if direction not in ("LONG", "SHORT"):
            return None

        spot, rows = self._normalize_option_chain(raw_chain)
        if not spot or not rows:
            return None

        atm = min(rows, key=lambda row: abs(row["strike"] - spot))
        option_type = "CE" if direction == "LONG" else "PE"
        prefix = "ce" if option_type == "CE" else "pe"

        ltp = atm.get(f"{prefix}_ltp")
        iv = atm.get(f"{prefix}_iv")
        oi = atm.get(f"{prefix}_oi")
        volume = atm.get(f"{prefix}_volume")

        return {
            "underlying": symbol,
            "expiry": expiry,
            "direction": direction,
            "option_type": option_type,
            "strike": atm["strike"],
            "contract_label": f"{symbol} {expiry} {int(atm['strike'])} {option_type}",
            "premium": ltp,
            "iv": iv,
            "open_interest": int(oi or 0),
            "volume": int(volume or 0),
            "underlying_ltp": spot,
            "selection_method": "ATM contract aligned with confirmed technical direction",
            "warning": "Research contract suggestion only. Groww option-chain volume may be unavailable or zero; verify live bid/ask spread, lot size, liquidity and slippage before execution.",
        }

    async def fno_confirm(
        self,
        symbol,
        timeframes,
        min_rr,
        expiry=None,
        include_market=True,
        take_snapshot=True,
    ):
        result = await super().fno_confirm(
            symbol,
            timeframes,
            min_rr,
            expiry=expiry,
            include_market=include_market,
            take_snapshot=take_snapshot,
        )

        session = self._market_session()
        result["market_session"] = session
        result["execution_ready"] = False
        result["execution_blockers"] = []

        if result.get("status") == "SETUP":
            chain = await self.option_chain(symbol, result.get("expiry"))
            option = self._recommended_option(
                symbol,
                chain["expiry"],
                chain["data"],
                result.get("technical", {}),
            )
            result["recommended_option"] = option

            if not session["is_open"]:
                result["execution_blockers"].append(
                    "NSE regular market is closed; underlying and option premiums may be stale."
                )
            if not option or not isinstance(option.get("premium"), (int, float)) or option.get("premium", 0) <= 0:
                result["execution_blockers"].append(
                    "No valid positive option premium is available for the recommended contract."
                )
            if not option or option.get("open_interest", 0) <= 0:
                result["execution_blockers"].append(
                    "Recommended contract has no reported open interest."
                )

            result["execution_ready"] = not result["execution_blockers"]

            # Keep technical/F&O analytics intact, but prevent the frontend from
            # presenting an executable BEST TRADE when the live execution gate
            # cannot be satisfied (for example outside market hours).
            if not result["execution_ready"]:
                result["status"] = "NO_TRADE"
        else:
            result["recommended_option"] = None

        return result
