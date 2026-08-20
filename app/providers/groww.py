import os
import time
import hashlib
import httpx


class GrowwProvider:
    BASE_URL = "https://api.groww.in"

    def __init__(self, settings):
        self.api_key = os.getenv("GROWW_API_KEY", "").strip()
        self.api_secret = os.getenv("GROWW_API_SECRET", "").strip()
        self.access_token = os.getenv("GROWW_ACCESS_TOKEN", "").strip()

        if not self.access_token and (not self.api_key or not self.api_secret):
            raise RuntimeError(
                "Set GROWW_ACCESS_TOKEN or both GROWW_API_KEY and GROWW_API_SECRET"
            )

    async def _get_access_token(self):
        # Use an explicitly supplied token if present.
        if self.access_token:
            return self.access_token

        timestamp = str(int(time.time()))
        checksum = hashlib.sha256(
            (self.api_secret + timestamp).encode("utf-8")
        ).hexdigest()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = {
            "key_type": "approval",
            "checksum": checksum,
            "timestamp": timestamp,
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.BASE_URL}/v1/token/api/access",
                headers=headers,
                json=payload,
            )

        response.raise_for_status()
        data = response.json()

        token = str(data.get("token", "")).strip()
        if not token:
            raise RuntimeError(
                f"Groww token generation failed: {data}"
            )

        return token

    def _instrument(self, symbol):
        # Initial mapping for common NSE cash/index instruments.
        cash_symbols = {
            "NIFTY": ("NSE", "CASH", "NIFTY"),
            "BANKNIFTY": ("NSE", "CASH", "BANKNIFTY"),
            "RELIANCE": ("NSE", "CASH", "RELIANCE"),
            "TCS": ("NSE", "CASH", "TCS"),
            "INFY": ("NSE", "CASH", "INFY"),
            "HDFCBANK": ("NSE", "CASH", "HDFCBANK"),
            "ICICIBANK": ("NSE", "CASH", "ICICIBANK"),
            "SBIN": ("NSE", "CASH", "SBIN"),
        }

        if symbol not in cash_symbols:
            raise ValueError(
                f"{symbol} is not mapped yet in GrowwProvider"
            )

        return cash_symbols[symbol]

    async def quote(self, symbol):
        token = await self._get_access_token()
        exchange, segment, trading_symbol = self._instrument(symbol)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-API-VERSION": "1.0",
        }

        params = {
            "exchange": exchange,
            "segment": segment,
            "trading_symbol": trading_symbol,
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.BASE_URL}/v1/live-data/quote",
                headers=headers,
                params=params,
            )

        response.raise_for_status()
        data = response.json()

        return {
            "provider": "GROWW",
            "symbol": symbol,
            "exchange": exchange,
            "segment": segment,
            "data": data,
        }

    async def option_chain(self, symbol, expiry=None):
        return {
            "provider": "GROWW",
            "symbol": symbol,
            "expiry": expiry,
            "status": "not_implemented_yet",
        }

    async def scan(self, symbols, timeframe, min_rr):
        results = []

        for symbol in symbols:
            try:
                quote = await self.quote(symbol)
                results.append({
                    "symbol": symbol,
                    "status": "ok",
                    "quote": quote,
                })
            except Exception as exc:
                results.append({
                    "symbol": symbol,
                    "status": "error",
                    "error": str(exc),
                })

        return {
            "provider": "GROWW",
            "timeframe": timeframe,
            "min_risk_reward": min_rr,
            "results": results,
        }
