from .mock import MockProvider
from .upstox import UpstoxProvider
from .groww_rate_limited import RateLimitedGrowwProvider


def get_provider(settings):
    provider = settings.market_data_provider.upper()

    if provider == "UPSTOX":
        return UpstoxProvider(settings)

    if provider == "GROWW":
        return RateLimitedGrowwProvider(settings)

    return MockProvider()
