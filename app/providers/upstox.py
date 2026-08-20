import httpx, os

class UpstoxProvider:
    def __init__(self, settings):
        self.token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        if not self.token:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN is required when MARKET_DATA_PROVIDER=UPSTOX")
        self.headers={"Accept":"application/json","Authorization":f"Bearer {self.token}"}
    async def quote(self, symbol):
        # Instrument-key mapping belongs in a provider configuration/database, not the browser.
        raise NotImplementedError("Configure instrument-key mapping before enabling live Upstox quotes")
    async def option_chain(self, symbol, expiry=None):
        raise NotImplementedError("Configure underlying instrument keys before enabling live option chains")
    async def scan(self, symbols, timeframe, min_rr):
        raise NotImplementedError("Live scanning requires quote/candle mappings and is disabled until configured")
