import unittest
from datetime import datetime, timezone

from app.defillama_stablecoin_provider import (
    STABLECOINS_URL,
    DefiLlamaStablecoinPolicy,
    DefiLlamaStablecoinProvider,
    architecture_contract,
)


def _t():
    return datetime(2026, 9, 5, 6, 30, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, *, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return _Response(self.payload)


class DefiLlamaStablecoinProviderTests(unittest.TestCase):
    def test_disabled_provider_makes_no_network_call(self):
        client = _Client({})
        provider = DefiLlamaStablecoinProvider(client=client)
        with self.assertRaises(RuntimeError):
            provider.capture_supply(first_seen_at=_t())
        self.assertEqual(client.calls, [])

    def test_capture_sums_only_requested_peg_type(self):
        payload = {
            "peggedAssets": [
                {"name": "Tether", "symbol": "USDT", "pegType": "peggedUSD", "circulating": {"peggedUSD": 100.0}, "price": 1.0},
                {"name": "USD Coin", "symbol": "USDC", "pegType": "peggedUSD", "circulating": {"peggedUSD": 50.0}, "price": 0.999},
                {"name": "Euro Stable", "symbol": "EURX", "pegType": "peggedEUR", "circulating": {"peggedEUR": 80.0}, "price": 1.1},
            ]
        }
        client = _Client(payload)
        provider = DefiLlamaStablecoinProvider(DefiLlamaStablecoinPolicy(enabled=True), client=client)
        capture = provider.capture_supply(first_seen_at=_t())
        self.assertEqual(capture.total_circulating, 150.0)
        self.assertEqual(capture.by_symbol, {"USDC": 50.0, "USDT": 100.0})
        self.assertEqual(capture.asset_count, 2)
        self.assertEqual(capture.first_seen_at, _t())
        self.assertEqual(client.calls[0]["url"], STABLECOINS_URL)
        self.assertEqual(client.calls[0]["params"]["includePrices"], "true")

    def test_duplicate_symbol_is_aggregated(self):
        payload = {"peggedAssets": [
            {"symbol": "USDT", "pegType": "peggedUSD", "circulating": {"peggedUSD": 100.0}},
            {"symbol": "USDT", "pegType": "peggedUSD", "circulating": {"peggedUSD": 25.0}},
        ]}
        provider = DefiLlamaStablecoinProvider(DefiLlamaStablecoinPolicy(enabled=True), client=_Client(payload))
        capture = provider.capture_supply(first_seen_at=_t())
        self.assertEqual(capture.by_symbol["USDT"], 125.0)
        self.assertEqual(capture.total_circulating, 125.0)

    def test_no_usable_usd_supply_fails_closed(self):
        payload = {"peggedAssets": [{"symbol": "EURX", "pegType": "peggedEUR", "circulating": {"peggedEUR": 10.0}}]}
        provider = DefiLlamaStablecoinProvider(DefiLlamaStablecoinPolicy(enabled=True), client=_Client(payload))
        with self.assertRaises(ValueError):
            provider.capture_supply(first_seen_at=_t())

    def test_contract_keeps_aggregate_supply_non_directional(self):
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertFalse(contract["api_key_required"])
        self.assertFalse(contract["historical_values_backdated_to_click"])
        self.assertFalse(contract["aggregate_supply_equals_exchange_inflow"])
        self.assertFalse(contract["aggregate_supply_is_directional_trade_signal"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
