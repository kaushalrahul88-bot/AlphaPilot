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
        """Return a CAS-aware NSE session phase for F&O-stock execution."""
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        current = now.time()
        weekday = now.weekday() < 5

        if not weekday or current < time(9, 15) or current > time(15, 40):
            phase = "CLOSED"
            execution_allowed = False
            description = "NSE equity derivatives session is closed."
            display_window = "MARKET CLOSED · F&O closes 15:40 IST"
        elif current < time(15, 15):
            phase = "CONTINUOUS"
            execution_allowed = True
            description = "Normal cash + F&O continuous market session."
            display_window = "CONTINUOUS MARKET · 09:15-15:15 IST"
        elif current <= time(15, 35):
            phase = "CLOSING_AUCTION"
            execution_allowed = False
            description = (
                "Underlying F&O stocks are in the NSE Closing Auction Session; "
                "cash price discovery is not treated as a normal continuous LTP."
            )
            display_window = "CLOSING AUCTION · 15:15-15:35 IST"
        else:
            phase = "FNO_ONLY"
            execution_allowed = False
            description = (
                "Cash closing auction has ended while equity derivatives remain "
                "tradable until 15:40; fresh AlphaPilot entries are blocked."
            )
            display_window = "F&O-ONLY WINDOW · 15:35-15:40 IST"

        return {
            "timezone": "Asia/Kolkata",
            "checked_at": now.isoformat(),
            # Existing frontend interprets is_open as whether fresh execution is allowed.
            "is_open": execution_allowed,
            "status": phase,
            "phase": phase,
            "execution_allowed": execution_allowed,
            "description": description,
            "regular_hours": display_window,
            "continuous_cash_hours": "09:15-15:15 IST, Monday-Friday",
            "closing_auction_window": "15:15-15:35 IST",
            "fno_only_window": "15:35-15:40 IST",
            "derivatives_close": "15:40 IST",
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

            if not session["execution_allowed"]:
                if session["phase"] == "CLOSING_AUCTION":
                    result["execution_blockers"].append(
                        "NSE Closing Auction Session is active (15:15-15:35 IST); the underlying cash price is in auction price discovery, so fresh BEST TRADE entries are blocked."
                    )
                elif session["phase"] == "FNO_ONLY":
                    result["execution_blockers"].append(
                        "F&O-only closing window is active (15:35-15:40 IST); the underlying cash market is no longer continuously trading, so fresh BEST TRADE entries are blocked."
                    )
                else:
                    result["execution_blockers"].append(
                        "NSE equity derivatives market is closed; underlying and option premiums may be stale."
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

            if not result["execution_ready"]:
                result["status"] = "NO_TRADE"
        else:
            result["recommended_option"] = None

        return result
