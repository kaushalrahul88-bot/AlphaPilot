from .mock import MockProvider
from .upstox import UpstoxProvider
from .groww_autoauth import AutoAuthAmountAwareGrowwProvider


def get_provider(settings):
    provider = settings.market_data_provider.upper()

    if provider == "UPSTOX":
        return UpstoxProvider(settings)

    if provider == "GROWW":
        return AutoAuthAmountAwareGrowwProvider(settings)

    return MockProvider()
