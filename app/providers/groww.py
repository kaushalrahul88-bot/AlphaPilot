import os
import httpx


class GrowwProvider:
    BASE_URL = "https://api.groww.in"

    def __init__(self, settings):
        self.api_key = os.getenv("GROWW_API_KEY", "")
        self.api_secret = os.getenv("GROWW_API_SECRET", "")
        self.access_token = os.getenv("GROWW_ACCESS_TOKEN", "")

        if not self.api_key:
            raise RuntimeError(
                "GROWW_API_KEY is required when MARKET_DATA_PROVIDER=GROWW"
            )

        self.headers = {
            "Accept": "application/json",
            "X-API-KEY": self.api_key,
        }

        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"

    async def quote(self, symbol):
        # Placeholder until full instrument mapping is added.
        return {
            "symbol": symbol,
            "provider": "GROWW",
            "status": "connected",
            "message": "Groww provider initialized. Live instrument mapping is the next step."
        }

    async def option_chain(self, symbol, expiry=None):
        return {
            "symbol": symbol,
            "expiry": expiry,
            "provider": "GROWW",
            "status": "pending"
        }

    async def scan(self, symbols, timeframe, min_rr):
        return {
            "provider": "GROWW",
            "timeframe": timeframe,
            "symbols": symbols,
            "status": "ready"
        }
