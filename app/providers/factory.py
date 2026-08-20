from .mock import MockProvider
from .upstox import UpstoxProvider

def get_provider(settings):
    provider = settings.market_data_provider.upper()
    if provider == "UPSTOX":
        return UpstoxProvider(settings)
    # Zerodha integration is intentionally server-side only; add after credentials/session flow are configured.
    return MockProvider()
