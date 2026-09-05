import unittest
from datetime import datetime, timedelta, timezone

import httpx

from app.coindcx_btc_public_provider import (
    BTC_USDT_PAIR,
    CoinDcxBtcProviderPolicy,
    CoinDcxBtcPublicProvider,
    architecture_contract,
    futures_candle_params,
    normalize_coindcx_futures_candles,
    normalize_coindcx_futures_rt,
    spot_candle_params,
)


def _t(hour=4, minute=0, second=0):
    return datetime(2026, 9, 5, hour, minute, second, tzinfo=timezone.utc)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload_by_url):
        self.payload_by_url = payload_by_url
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return _FakeResponse(self.payload_by_url[url])


class CoinDcxBtcPublicProviderTests(unittest.TestCase):
    def test_collection_is_disabled_by_default(self):
        provider = CoinDcxBtcPublicProvider()
        with self.assertRaises(RuntimeError):
            provider.fetch_spot_candles(interval="1m")

    def test_spot_params_use_documented_pair_and_millisecond_bounds(self):
        params = spot_candle_params(interval="1h", start_at=_t(1), end_at=_t(3), limit=500)
        self.assertEqual(params["pair"], BTC_USDT_PAIR)
        self.assertEqual(params["interval"], "1h")
        self.assertEqual(params["startTime"], int(_t(1).timestamp() * 1000))
        self.assertEqual(params["endTime"], int(_t(3).timestamp() * 1000))
        self.assertEqual(params["limit"], 500)

    def test_spot_limit_above_documented_max_is_rejected(self):
        with self.assertRaises(ValueError):
            spot_candle_params(interval="1m", limit=1001)

    def test_futures_params_use_seconds_and_pcode_f(self):
        params = futures_candle_params(resolution="60", start_at=_t(1), end_at=_t(3))
        self.assertEqual(params["pair"], BTC_USDT_PAIR)
        self.assertEqual(params["from"], int(_t(1).timestamp()))
        self.assertEqual(params["to"], int(_t(3).timestamp()))
        self.assertEqual(params["resolution"], "60")
        self.assertEqual(params["pcode"], "f")

    def test_futures_candle_open_time_is_not_visibility_time(self):
        open_at = _t(1)
        payload = {
            "s": "ok",
            "data": [{
                "open": 100_000.0,
                "high": 100_100.0,
                "low": 99_900.0,
                "close": 100_050.0,
                "volume": 42.0,
                "time": int(open_at.timestamp() * 1000),
            }],
        }
        rows = normalize_coindcx_futures_candles(payload, resolution="60")
        self.assertEqual(rows[0].open_at, open_at)
        self.assertEqual(rows[0].available_at, open_at + timedelta(hours=1))
        self.assertTrue(rows[0].provenance.reconstructible_public_data)

    def test_rt_funding_capture_uses_first_seen_not_provider_time_as_availability(self):
        first_seen = _t(4, 0, 5)
        provider_ts = _t(4, 0, 0)
        payload = {
            "ts": int(provider_ts.timestamp() * 1000),
            "prices": {
                BTC_USDT_PAIR: {
                    "fr": 0.0001,
                    "efr": 0.00012,
                    "mp": 100_010.0,
                    "ls": 100_000.0,
                    "pc": 2.5,
                    "v": 12345.0,
                    "mkt": "BTCUSDT",
                    "btST": int(provider_ts.timestamp() * 1000),
                    "bmST": int(provider_ts.timestamp() * 1000),
                }
            },
        }
        capture = normalize_coindcx_futures_rt(payload, first_seen_at=first_seen)
        self.assertEqual(capture.first_seen_at, first_seen)
        self.assertEqual(capture.provider_snapshot_at, provider_ts)
        self.assertEqual(capture.provenance.availability_basis, "FIRST_SEEN_CAPTURE")
        self.assertFalse(capture.provenance.reconstructible_public_data)

    def test_rt_capture_does_not_infer_oi_or_liquidations_or_direction(self):
        payload = {
            "ts": int(_t().timestamp() * 1000),
            "prices": {
                BTC_USDT_PAIR: {"fr": 0.001, "mp": 100_000.0, "ls": 100_000.0, "pc": 5.0, "v": 999999.0}
            },
        }
        evidence = normalize_coindcx_futures_rt(payload, first_seen_at=_t()).context_evidence()
        self.assertTrue(evidence.context_only)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertFalse(evidence.metadata["open_interest_inferred"])
        self.assertFalse(evidence.metadata["liquidations_inferred"])
        self.assertFalse(evidence.metadata["may_generate_futures_trade"])

    def test_enabled_provider_can_use_injected_client_without_live_network(self):
        from app.coindcx_btc_public_provider import SPOT_CANDLES_URL

        open_at = _t(1)
        fake = _FakeClient({
            SPOT_CANDLES_URL: [{
                "open": 100_000.0, "high": 100_100.0, "low": 99_900.0,
                "close": 100_050.0, "volume": 2.0,
                "time": int(open_at.timestamp() * 1000),
            }]
        })
        provider = CoinDcxBtcPublicProvider(CoinDcxBtcProviderPolicy(enabled=True), client=fake)
        rows = provider.fetch_spot_candles(interval="1h", start_at=open_at, end_at=open_at + timedelta(hours=2))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].available_at, open_at + timedelta(hours=1))
        self.assertEqual(len(fake.calls), 1)

    def test_architecture_contract_does_not_claim_options_or_execution(self):
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertTrue(contract["spot_history_supported"])
        self.assertTrue(contract["futures_candle_history_supported"])
        self.assertTrue(contract["current_futures_funding_capture_supported"])
        self.assertFalse(contract["current_futures_snapshot_may_be_backdated"])
        self.assertFalse(contract["historical_options_api_claimed"])
        self.assertFalse(contract["options_execution_enabled"])
        self.assertFalse(contract["futures_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
