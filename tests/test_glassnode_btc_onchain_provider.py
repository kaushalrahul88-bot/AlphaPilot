import unittest
from datetime import datetime, timezone

from app.glassnode_btc_onchain_provider import (
    BASE_URL,
    METRICS,
    GlassnodeBtcOnchainPolicy,
    GlassnodeBtcOnchainProvider,
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
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def get(self, url, *, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return _Response(self.payloads[url])


class GlassnodeBtcOnchainProviderTests(unittest.TestCase):
    def _provider(self):
        current = int(_t().timestamp())
        earlier = current - 3600
        payloads = {
            BASE_URL + spec["path"]: [
                {"t": earlier, "v": 1.25 if metric == "MVRV" else 1.01 if metric == "SOPR" else 125.0},
                {"t": current + 1, "v": 999.0},
            ]
            for metric, spec in METRICS.items()
        }
        client = _Client(payloads)
        provider = GlassnodeBtcOnchainProvider(
            GlassnodeBtcOnchainPolicy(enabled=True, api_key="secret", interval="1h"),
            client=client,
        )
        return provider, client

    def test_disabled_provider_makes_no_network_call(self):
        client = _Client({})
        provider = GlassnodeBtcOnchainProvider(client=client)
        with self.assertRaises(RuntimeError):
            provider.capture_metric("MVRV", first_seen_at=_t())
        self.assertEqual(client.calls, [])

    def test_enabled_provider_requires_api_key(self):
        with self.assertRaises(ValueError):
            GlassnodeBtcOnchainPolicy(enabled=True).validated()

    def test_pit_mvrv_is_marked_content_immutable_but_first_seen_remains_now(self):
        provider, client = self._provider()
        capture = provider.capture_metric("MVRV", first_seen_at=_t())
        self.assertEqual(capture.value, 1.25)
        self.assertTrue(capture.historical_content_immutable)
        self.assertLess(capture.provider_time, capture.first_seen_at)
        call = client.calls[-1]
        self.assertEqual(call["headers"]["X-Api-Key"], "secret")
        self.assertEqual(call["params"]["a"], "BTC")
        self.assertEqual(call["params"]["i"], "1h")

    def test_exchange_netflow_is_explicitly_mutable_entity_metric(self):
        provider, _ = self._provider()
        capture = provider.capture_metric("EXCHANGE_NETFLOW", first_seen_at=_t())
        self.assertFalse(capture.historical_content_immutable)
        self.assertEqual(capture.unit, "BTC")

    def test_future_provider_row_is_never_selected(self):
        provider, _ = self._provider()
        for metric in METRICS:
            capture = provider.capture_metric(metric, first_seen_at=_t())
            self.assertLessEqual(capture.provider_time, _t())
            self.assertNotEqual(capture.value, 999.0)

    def test_metric_not_enabled_by_policy_fails_closed(self):
        provider = GlassnodeBtcOnchainProvider(
            GlassnodeBtcOnchainPolicy(enabled=True, api_key="secret", metrics=("MVRV",)),
            client=_Client({}),
        )
        with self.assertRaises(ValueError):
            provider.capture_metric("SOPR", first_seen_at=_t())

    def test_contract_distinguishes_pit_immutability_from_delivery_time(self):
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertIn("MVRV", contract["pit_metric_history_immutable"])
        self.assertIn("EXCHANGE_NETFLOW", contract["mutable_entity_label_metrics"])
        self.assertFalse(contract["pit_content_immutability_equals_exact_delivery_time"])
        self.assertTrue(contract["first_seen_at_still_required_for_click_replay"])
        self.assertFalse(contract["raw_onchain_metric_is_trade_signal"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
