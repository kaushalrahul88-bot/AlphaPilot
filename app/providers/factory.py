from .mock import MockProvider
from .upstox import UpstoxProvider
from .groww_memory_safe import MemorySafeGrowwProvider


def get_provider(settings):
    provider = settings.market_data_provider.upper()

    if provider == "UPSTOX":
        return UpstoxProvider(settings)

    if provider == "GROWW":
        return MemorySafeGrowwProvider(settings)

    return MockProvider()
