import httpx
import pytest

import app.commodity_history_auth_safe as safe


class DummyProvider:
    def __init__(self):
        self.throttle_calls = 0
        self.rate_limit_calls = 0

    async def _throttle(self):
        self.throttle_calls += 1

    async def _register_rate_limit(self):
        self.rate_limit_calls += 1


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.groww.in/test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


@pytest.mark.asyncio
async def test_401_refreshes_once_and_retries_same_fetch(monkeypatch):
    provider = DummyProvider()
    attempts = 0
    refreshes = 0
    expected = [["2026-09-07T11:00:00+05:30", 1, 2, 0.5, 1.5, 10]]

    async def fake_fetch(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _http_error(401)
        return expected

    async def fake_refresh(value):
        nonlocal refreshes
        assert value is provider
        refreshes += 1
        return "TOTP"

    monkeypatch.setattr(safe, "_fetch_chunked", fake_fetch)
    monkeypatch.setattr(safe, "_refresh_after_401", fake_refresh)

    result = await safe.fetch_chunked_auth_safe(
        provider,
        {"trading_symbol": "COPPER30SEP26FUT"},
        5,
        object(),
        object(),
    )

    assert result == expected
    assert attempts == 2
    assert refreshes == 1
    assert provider.throttle_calls == 2
    assert provider.rate_limit_calls == 0


@pytest.mark.asyncio
async def test_persistent_401_fails_closed_after_one_refresh(monkeypatch):
    provider = DummyProvider()
    attempts = 0
    refreshes = 0

    async def fake_fetch(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise _http_error(401)

    async def fake_refresh(value):
        nonlocal refreshes
        refreshes += 1
        return "TOTP"

    monkeypatch.setattr(safe, "_fetch_chunked", fake_fetch)
    monkeypatch.setattr(safe, "_refresh_after_401", fake_refresh)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await safe.fetch_chunked_auth_safe(
            provider,
            {"trading_symbol": "COPPER30SEP26FUT"},
            5,
            object(),
            object(),
        )

    assert caught.value.response.status_code == 401
    assert attempts == 2
    assert refreshes == 1
    assert provider.throttle_calls == 2


@pytest.mark.asyncio
async def test_429_registers_shared_cooldown_without_auth_refresh(monkeypatch):
    provider = DummyProvider()
    refreshes = 0

    async def fake_fetch(*args, **kwargs):
        raise _http_error(429)

    async def fake_refresh(value):
        nonlocal refreshes
        refreshes += 1

    monkeypatch.setattr(safe, "_fetch_chunked", fake_fetch)
    monkeypatch.setattr(safe, "_refresh_after_401", fake_refresh)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await safe.fetch_chunked_auth_safe(
            provider,
            {"trading_symbol": "COPPER30SEP26FUT"},
            5,
            object(),
            object(),
        )

    assert caught.value.response.status_code == 429
    assert refreshes == 0
    assert provider.throttle_calls == 1
    assert provider.rate_limit_calls == 1


def test_architecture_contract_is_operational_only():
    contract = safe.architecture_contract()
    assert contract["historical_fetch_semantics_changed"] is False
    assert contract["strategy_rules_changed"] is False
    assert contract["contract_selection_changed"] is False
    assert contract["pit_visibility_changed"] is False
    assert contract["retry_on_401"] == 1
    assert contract["shared_throttle_used"] is True
    assert contract["shared_429_cooldown_registered"] is True
    assert contract["live_execution_enabled"] is False
    assert contract["broker_order_placement_enabled"] is False
    assert contract["capital_committed"] == 0
