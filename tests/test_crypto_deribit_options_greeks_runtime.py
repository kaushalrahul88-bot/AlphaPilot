import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_deribit_options_greeks_runtime import (
    ENV_DERIBIT_GREEKS_ENABLED,
    DeribitOptionsGreeksRuntimeConfig,
    architecture_contract,
    build_deribit_options_greeks_runtime,
    initialize_deribit_options_greeks_runtime,
    run_deribit_options_greeks_service,
    runtime_status,
)
from app.deribit_btc_options_context_provider import INSTRUMENTS_URL


def _t():
    return datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _instrument(name, option_type, strike):
    expiry = _t() + timedelta(days=2)
    return {
        "instrument_name": name,
        "kind": "option",
        "is_active": True,
        "state": "open",
        "expiration_timestamp": int(expiry.timestamp() * 1000),
        "strike": strike,
        "option_type": option_type,
    }


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _HttpClient:
    def __init__(self):
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        if url != INSTRUMENTS_URL:
            raise AssertionError(f"unexpected HTTP endpoint: {url}")
        return _Response({
            "jsonrpc": "2.0",
            "result": [
                _instrument("BTC-C", "call", 108_000),
                _instrument("BTC-P", "put", 92_000),
            ],
        })


class _Store:
    def __init__(self):
        self.initialize_calls = 0
        self.records = []

    async def initialize(self):
        self.initialize_calls += 1
        return {"status": "BTC_PIT_POSTGRES_SCHEMA_READY"}

    async def insert_first_seen(self, record):
        self.records.append(record)
        return {"status": "INSERTED_FIRST_SEEN", "record_fingerprint": record.record_fingerprint}


class _WebSocket:
    def __init__(self):
        self.sent = []
        self.ack_sent = False

    async def send(self, value):
        self.sent.append(value)

    async def recv(self):
        if self.ack_sent:
            raise AssertionError("runtime test should stop after subscription acknowledgement")
        self.ack_sent = True
        request = json.loads(self.sent[-1])
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": request["params"]["channels"]})


class _Connector:
    def __init__(self):
        self.urls = []
        self.websocket = _WebSocket()

    def __call__(self, url):
        self.urls.append(url)
        websocket = self.websocket

        class _Context:
            async def __aenter__(self):
                return websocket

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Context()


class DeribitOptionsGreeksRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_config_is_disabled_and_network_free(self):
        config = DeribitOptionsGreeksRuntimeConfig.from_env({})
        self.assertFalse(config.archive_enabled)
        self.assertFalse(config.greeks_enabled)
        runtime = build_deribit_options_greeks_runtime(config)
        self.assertEqual(runtime["status"], "DERIBIT_OPTIONS_GREEKS_RUNTIME_DISABLED")
        self.assertIsNone(runtime["store"])
        self.assertIsNone(runtime["instrument_provider"])
        self.assertIsNone(runtime["service"])
        status = runtime_status(config)
        self.assertFalse(status["network_request_performed"])
        self.assertFalse(status["instrument_seeded"])
        self.assertFalse(status["websocket_opened"])
        self.assertFalse(status["trade_generation_enabled"])

    def test_greeks_stream_requires_archive_and_database(self):
        with self.assertRaises(ValueError):
            DeribitOptionsGreeksRuntimeConfig(
                archive_enabled=False,
                database_url="postgresql://example/db",
                greeks_enabled=True,
            ).validated()
        with self.assertRaises(ValueError):
            DeribitOptionsGreeksRuntimeConfig(
                archive_enabled=True,
                database_url="",
                greeks_enabled=True,
            ).validated()
        with self.assertRaises(ValueError):
            DeribitOptionsGreeksRuntimeConfig(
                archive_enabled=True,
                database_url="",
                greeks_enabled=False,
            ).validated()

    def test_runtime_policy_validation_fails_closed(self):
        with self.assertRaises(ValueError):
            DeribitOptionsGreeksRuntimeConfig(ticker_interval="raw").validated()
        with self.assertRaises(ValueError):
            DeribitOptionsGreeksRuntimeConfig(max_expiries=0).validated()
        with self.assertRaises(ValueError):
            DeribitOptionsGreeksRuntimeConfig(max_channels=0).validated()
        with self.assertRaises(ValueError):
            DeribitOptionsGreeksRuntimeConfig(archive_min_interval_seconds=0).validated()
        with self.assertRaises(ValueError):
            DeribitOptionsGreeksRuntimeConfig.from_env({
                "ALPHAPILOT_CRYPTO_DERIBIT_OPTIONS_GREEKS_MAX_EXPIRIES": "not-an-int"
            })

    def test_archive_only_build_does_not_seed_instruments_or_open_socket(self):
        http = _HttpClient()
        store = _Store()
        config = DeribitOptionsGreeksRuntimeConfig(
            archive_enabled=True,
            database_url="postgresql://example/db",
            greeks_enabled=False,
        )
        runtime = build_deribit_options_greeks_runtime(config, http_client=http, store=store)
        self.assertEqual(runtime["status"], "DERIBIT_OPTIONS_GREEKS_ARCHIVE_ONLY_READY")
        self.assertIs(runtime["store"], store)
        self.assertEqual(http.calls, [])
        self.assertFalse(runtime["instrument_provider"].policy.enabled)
        self.assertFalse(runtime["stream_policy"].enabled)
        self.assertIsNone(runtime["service"])

    async def test_initialize_only_initializes_schema_and_performs_no_network(self):
        http = _HttpClient()
        store = _Store()
        config = DeribitOptionsGreeksRuntimeConfig(
            archive_enabled=True,
            database_url="postgresql://example/db",
            greeks_enabled=True,
        )
        result = await initialize_deribit_options_greeks_runtime(
            config,
            http_client=http,
            clock=_t,
            store=store,
        )
        self.assertEqual(result["status"], "DERIBIT_OPTIONS_GREEKS_RUNTIME_READY")
        self.assertTrue(result["schema_initialized"])
        self.assertFalse(result["instrument_seeded"])
        self.assertFalse(result["stream_started"])
        self.assertFalse(result["network_request_performed"])
        self.assertEqual(store.initialize_calls, 1)
        self.assertEqual(http.calls, [])

    async def test_disabled_run_service_never_seeds_or_connects(self):
        http = _HttpClient()
        store = _Store()
        connector = _Connector()
        result = await run_deribit_options_greeks_service(
            DeribitOptionsGreeksRuntimeConfig(),
            stop_event=asyncio.Event(),
            http_client=http,
            clock=_t,
            websocket_connector=connector,
            store=store,
        )
        self.assertEqual(result["status"], "DERIBIT_OPTIONS_GREEKS_RUNTIME_DISABLED")
        self.assertFalse(result["instrument_seeded"])
        self.assertFalse(result["stream_started"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(store.initialize_calls, 0)
        self.assertEqual(http.calls, [])
        self.assertEqual(connector.urls, [])

    async def test_explicit_run_seeds_once_then_opens_documented_public_stream(self):
        http = _HttpClient()
        store = _Store()
        connector = _Connector()
        stop = asyncio.Event()
        stop.set()
        config = DeribitOptionsGreeksRuntimeConfig(
            archive_enabled=True,
            database_url="postgresql://example/db",
            greeks_enabled=True,
            max_expiries=1,
        )
        result = await run_deribit_options_greeks_service(
            config,
            stop_event=stop,
            http_client=http,
            clock=_t,
            websocket_connector=connector,
            store=store,
        )
        self.assertEqual(result["status"], "DERIBIT_OPTIONS_GREEKS_STREAM_STOPPED")
        self.assertTrue(result["instrument_seeded"])
        self.assertEqual(result["seeded_instrument_count"], 2)
        self.assertTrue(result["stream_started"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(store.initialize_calls, 1)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(http.calls[0][0], INSTRUMENTS_URL)
        self.assertEqual(http.calls[0][1], {"currency": "BTC", "kind": "option", "expired": "false"})
        self.assertEqual(len(connector.urls), 1)
        request = json.loads(connector.websocket.sent[0])
        self.assertEqual(request["method"], "public/subscribe")
        self.assertTrue(all(channel.startswith("ticker.") for channel in request["params"]["channels"]))

    def test_architecture_enforces_separate_activation_and_no_execution(self):
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertEqual(contract["separate_environment_switch"], ENV_DERIBIT_GREEKS_ENABLED)
        self.assertTrue(contract["archive_required_before_stream"])
        self.assertTrue(contract["database_required_before_stream"])
        self.assertFalse(contract["schema_initialization_starts_stream"])
        self.assertFalse(contract["status_check_performs_network_request"])
        self.assertFalse(contract["build_performs_network_request"])
        self.assertTrue(contract["instrument_seed_occurs_only_when_service_runs"])
        self.assertFalse(contract["periodic_deribit_context_switch_enables_greeks_stream"])
        self.assertFalse(contract["coindcx_options_switch_enables_greeks_stream"])
        self.assertFalse(contract["greeks_stream_enables_coindcx_execution"])
        self.assertFalse(contract["coindcx_contract_selection_enabled"])
        self.assertFalse(contract["coindcx_quote_fill_enabled"])
        self.assertFalse(contract["options_trade_generation_enabled"])
        self.assertFalse(contract["futures_trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
