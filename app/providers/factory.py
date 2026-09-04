from .mock import MockProvider
from .upstox import UpstoxProvider
from .groww_account_safe import AccountSafeGrowwProvider


def get_provider(settings):
    provider = settings.market_data_provider.upper()

    if provider == "UPSTOX":
        return UpstoxProvider(settings)

    if provider == "GROWW":
        return AccountSafeGrowwProvider(settings)

    return MockProvider()
