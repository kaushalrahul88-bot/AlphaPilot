from .mock import MockProvider
from .upstox import UpstoxProvider
from .groww_autoauth import AutoAuthAmountAwareGrowwProvider


_PROVIDER_CACHE = {}


def get_provider(settings):
    """Return one process-wide provider instance per configured provider.

    AlphaPilot calls get_provider() for every API request. Recreating the Groww
    provider on every scanner batch also recreates instance auth state and can
    trigger repeated token generation during a 44-symbol live scan. Reusing the
    provider keeps auth/session caches warm and reduces pressure on Groww/Render.
    """
    provider = settings.market_data_provider.upper()

    cached = _PROVIDER_CACHE.get(provider)
    if cached is not None:
        return cached

    if provider == "UPSTOX":
        instance = UpstoxProvider(settings)
    elif provider == "GROWW":
        instance = AutoAuthAmountAwareGrowwProvider(settings)
    else:
        instance = MockProvider()

    _PROVIDER_CACHE[provider] = instance
    return instance
