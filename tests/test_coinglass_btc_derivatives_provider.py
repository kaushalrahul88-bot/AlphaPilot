import unittest
from datetime import datetime, timezone

from app.coinglass_btc_derivatives_provider import (
    CoinGlassBtcDerivativesPolicy,
    CoinGlassBtcDerivativesProvider,
    OI_AGGREGATED_HISTORY_URL,
    LIQUIDATION_AGGREGATED_HISTORY_URL,
    architecture_contract,
)


def _t():
    return datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def get(self, url, *, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return _Response(self.payloads[url])


class CoinGlassBtcDerivativesProviderTests(unittest.TestCase):
    def _provider(self):
        now_ms = int(_t().timestamp() * 1000)
        earlier = now_ms - 4 * 60 * 60 * 1000
        payloads = {
            OI_AGGREGATED_HISTORY_URL: {
                "code": "0",
                "msg": "success",
                "data": [
                    {"time": earlier, "open": "100", "high": "130", "low": "90", "close": "120"},
                    {"time": now_ms + 1, "open": "200", "high": "230", "low": "190", "close": "220"},
                ],
            },
            LIQUIDATION_AGGREGATED_HISTORY_URL: {
                "code": "0",
                "msg": "success",
                "data": [
                    {
                        "time": earlier,
                        "aggregated_long_liquidation_usd": 5_000_000,
                        "aggregated_short_liquidation_usd": 7_000_000,
                    },
                    {
                        "time": now_ms + 1,
                        "aggregated_long_liquidation_usd": 50_000_000,
                        "aggregated_short_liquidation_usd": 70_000_000,
                    },
                ],
            },
        }
        client = _Client(payloads)
        provider = CoinGlassBtcDerivativesProvider(
            CoinGlassBtcDerivativesPolicy(enabled=True, api_key="secret", interval="4h"),
            client=client,
        )
        return provider, client

    def test_disabled_provider_fails_before_network(self):
        client = _Client({})
        provider = CoinGlassBtcDerivativesProvider(client=client)
        with self.assertRaises(RuntimeError):
            provider.capture_open_interest(first_seen_at=_t())
        self.assertEqual(client.calls, [])

    def test_enabled_policy_requires_api_key(self):
        with self.assertRaises(ValueError):
            CoinGlassBtcDerivativesPolicy(enabled=True).validated()

    def test_open_interest_capture_uses_latest_row_visible_by_first_seen(self):
        provider, client = self._provider()
        capture = provider.capture_open_interest(first_seen_at=_t())
        self.assertEqual(capture.open_interest_close_usd, 120.0)
        self.assertLessEqual(capture.provider_time, capture.first_seen_at)
        call = client.calls[-1]
        self.assertEqual(call["url"], OI_AGGREGATED_HISTORY_URL)
        self.assertEqual(call["headers"]["CG-API-KEY"], "secret")
        self.assertEqual(call["params"]["symbol"], "BTC")
        self.assertEqual(call["params"]["unit"], "usd")

    def test_liquidation_capture_uses_documented_aggregated_fields(self):
        provider, client = self._provider()
        capture = provider.capture_liquidations(first_seen_at=_t())
        self.assertEqual(capture.long_liquidation_usd, 5_000_000.0)
        self.assertEqual(capture.short_liquidation_usd, 7_000_000.0)
        call = client.calls[-1]
        self.assertEqual(call["url"], LIQUIDATION_AGGREGATED_HISTORY_URL)
        self.assertEqual(call["params"]["exchange_list"], "Binance,OKX,Bybit")

    def test_future_provider_row_is_never_selected(self):
        provider, _ = self._provider()
        oi = provider.capture_open_interest(first_seen_at=_t())
        liq = provider.capture_liquidations(first_seen_at=_t())
        self.assertLessEqual(oi.provider_time, _t())
        self.assertLessEqual(liq.provider_time, _t())

    def test_architecture_contract_keeps_history_and_execution_fail_closed(self):
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertTrue(contract["api_key_required_when_enabled"])
        self.assertFalse(contract["historical_fetch_is_automatically_treated_as_historical_pit"])
        self.assertFalse(contract["open_interest_inferred_from_volume"])
        self.assertFalse(contract["liquidations_inferred_from_price"])
        self.assertFalse(contract["options_trade_generation_allowed"])
        self.assertFalse(contract["futures_trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
