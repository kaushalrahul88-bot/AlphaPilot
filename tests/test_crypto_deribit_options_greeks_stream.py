import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_deribit_options_greeks_stream import (
    DERIBIT_PRODUCTION_WS_URL,
    DeribitOptionsGreeksStreamPolicy,
    DeribitOptionsGreeksStreamService,
    architecture_contract,
    build_public_subscribe_request,
    select_stream_instruments,
)
from app.deribit_btc_options_ticker_greeks import DeribitOptionInstrumentMeta


def _t(seconds=0):
    return datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def _meta(name, option_type, *, strike, days):
    return DeribitOptionInstrumentMeta(
        instrument_name=name,
        option_type=option_type,
        strike=strike,
        expiry_at=_t() + timedelta(days=days),
    ).validated()


def _instruments():
    rows = [
        _meta("BTC-E1-C25", "call", strike=108_000, days=2),
        _meta("BTC-E1-P25", "put", strike=92_000, days=2),
        _meta("BTC-E1-C50", "call", strike=100_000, days=2),
        _meta("BTC-E1-P50", "put", strike=100_000, days=2),
        _meta("BTC-E2-C25", "call", strike=109_000, days=9),
        _meta("BTC-E2-P25", "put", strike=91_000, days=9),
        _meta("BTC-E3-C25", "call", strike=110_000, days=16),
        _meta("BTC-E3-P25", "put", strike=90_000, days=16),
    ]
    return {row.instrument_name: row for row in rows}


def _ticker(meta, *, delta, iv, provider_at=None):
    provider_at = provider_at or _t()
    return {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {
            "channel": f"ticker.{meta.instrument_name}.agg2",
            "data": {
                "instrument_name": meta.instrument_name,
                "timestamp": int(provider_at.timestamp() * 1000),
                "underlying_price": 100_000,
                "mark_iv": iv,
                "bid_iv": iv - 1,
                "ask_iv": iv + 1,
                "open_interest": 5,
                "greeks": {
                    "delta": delta,
                    "gamma": 0.00001,
                    "theta": -0.001,
                    "vega": 0.02,
                    "rho": 0.01,
                },
            },
        },
    }


class _Store:
    def __init__(self):
        self.records = []

    async def insert_first_seen(self, record):
        self.records.append(record)
        return {"status": "INSERTED_FIRST_SEEN", "record_fingerprint": record.record_fingerprint}


class _Clock:
    def __init__(self, values):
        self.values = list(values)
        self.last = self.values[-1]

    def __call__(self):
        if self.values:
            self.last = self.values.pop(0)
        return self.last


class _WebSocket:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []

    async def send(self, value):
        self.sent.append(value)

    async def recv(self):
        if not self.incoming:
            raise AssertionError("fake websocket has no queued message")
        return self.incoming.pop(0)


class _Connector:
    def __init__(self, websocket):
        self.websocket = websocket
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        websocket = self.websocket

        class _Context:
            async def __aenter__(self):
                return websocket

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Context()


class CryptoDeribitOptionsGreeksStreamTests(unittest.IsolatedAsyncioTestCase):
    def test_nearest_expiry_selection_uses_whole_strike_sets_not_delta(self):
        selected = select_stream_instruments(
            _instruments(),
            as_of=_t(),
            policy=DeribitOptionsGreeksStreamPolicy(max_expiries=2),
        )
        self.assertEqual(set(selected), {
            "BTC-E1-C25", "BTC-E1-P25", "BTC-E1-C50", "BTC-E1-P50",
            "BTC-E2-C25", "BTC-E2-P25",
        })
        self.assertNotIn("BTC-E3-C25", selected)

    def test_selection_fails_closed_when_channel_budget_is_exceeded(self):
        with self.assertRaises(ValueError):
            select_stream_instruments(
                _instruments(),
                as_of=_t(),
                policy=DeribitOptionsGreeksStreamPolicy(max_expiries=2, max_channels=2),
            )

    def test_subscription_request_uses_documented_public_ticker_channels(self):
        selected = select_stream_instruments(
            _instruments(), as_of=_t(), policy=DeribitOptionsGreeksStreamPolicy(max_expiries=1)
        )
        request = build_public_subscribe_request(selected)
        self.assertEqual(request["jsonrpc"], "2.0")
        self.assertEqual(request["method"], "public/subscribe")
        self.assertEqual(request["id"], 42)
        self.assertTrue(all(channel.startswith("ticker.BTC-E1-") and channel.endswith(".agg2") for channel in request["params"]["channels"]))

    async def test_disabled_session_opens_no_connection(self):
        connector = _Connector(_WebSocket([]))
        service = DeribitOptionsGreeksStreamService(
            instruments=_instruments(),
            store=_Store(),
            websocket_connector=connector,
        )
        result = await service.run_session(asyncio.Event())
        self.assertEqual(result["status"], "DERIBIT_OPTIONS_GREEKS_STREAM_DISABLED")
        self.assertFalse(result["connection_opened"])
        self.assertEqual(connector.urls, [])

    async def test_two_real_delta_tickers_create_one_archived_25d_snapshot(self):
        rows = _instruments()
        store = _Store()
        service = DeribitOptionsGreeksStreamService(
            instruments=rows,
            store=store,
            policy=DeribitOptionsGreeksStreamPolicy(enabled=True, max_expiries=1),
            clock=_Clock([_t(1), _t(2)]),
        )
        first = await service.process_message(_ticker(rows["BTC-E1-C25"], delta=0.24, iv=52))
        second = await service.process_message(_ticker(rows["BTC-E1-P25"], delta=-0.26, iv=58))
        self.assertEqual(first["status"], "DERIBIT_GREEKS_TICKER_CAPTURED_NO_25D_PAIR")
        self.assertEqual(second["status"], "DERIBIT_GREEKS_25D_ARCHIVED")
        self.assertEqual(second["skew_25d"], 6.0)
        self.assertEqual(len(store.records), 1)
        payload = store.records[0].payload
        self.assertTrue(payload["skew_25d_observed_from_ticker_delta"])
        self.assertFalse(payload["skew_25d_inferred_from_strike"])
        self.assertFalse(second["coindcx_contract_selection_allowed"])
        self.assertFalse(second["coindcx_quote_fill_allowed"])
        self.assertFalse(second["coindcx_pnl_replay_allowed"])
        self.assertFalse(second["trade_generated"])

    async def test_bad_delta_pair_is_not_estimated_or_archived(self):
        rows = _instruments()
        store = _Store()
        service = DeribitOptionsGreeksStreamService(
            instruments=rows,
            store=store,
            policy=DeribitOptionsGreeksStreamPolicy(enabled=True, max_expiries=1, max_delta_distance=0.03),
            clock=_Clock([_t(1), _t(2)]),
        )
        await service.process_message(_ticker(rows["BTC-E1-C50"], delta=0.50, iv=50))
        result = await service.process_message(_ticker(rows["BTC-E1-P50"], delta=-0.50, iv=55))
        self.assertEqual(result["status"], "DERIBIT_GREEKS_TICKER_CAPTURED_NO_25D_PAIR")
        self.assertEqual(store.records, [])

    async def test_archive_is_throttled_after_a_valid_pair(self):
        rows = _instruments()
        store = _Store()
        service = DeribitOptionsGreeksStreamService(
            instruments=rows,
            store=store,
            policy=DeribitOptionsGreeksStreamPolicy(enabled=True, max_expiries=1, archive_min_interval_seconds=10),
            clock=_Clock([_t(1), _t(2), _t(3)]),
        )
        await service.process_message(_ticker(rows["BTC-E1-C25"], delta=0.24, iv=52))
        archived = await service.process_message(_ticker(rows["BTC-E1-P25"], delta=-0.26, iv=58))
        throttled = await service.process_message(_ticker(rows["BTC-E1-C25"], delta=0.25, iv=53, provider_at=_t(2)))
        self.assertEqual(archived["status"], "DERIBIT_GREEKS_25D_ARCHIVED")
        self.assertEqual(throttled["status"], "DERIBIT_GREEKS_25D_ARCHIVE_THROTTLED")
        self.assertEqual(len(store.records), 1)

    async def test_run_session_sends_subscription_and_validates_ack(self):
        rows = _instruments()
        selected = select_stream_instruments(
            rows, as_of=_t(), policy=DeribitOptionsGreeksStreamPolicy(enabled=True, max_expiries=1)
        )
        request = build_public_subscribe_request(selected)
        ack = {"jsonrpc": "2.0", "id": 42, "result": request["params"]["channels"]}
        websocket = _WebSocket([json.dumps(ack)])
        connector = _Connector(websocket)
        stop = asyncio.Event()
        stop.set()
        service = DeribitOptionsGreeksStreamService(
            instruments=rows,
            store=_Store(),
            policy=DeribitOptionsGreeksStreamPolicy(enabled=True, max_expiries=1),
            clock=lambda: _t(),
            websocket_connector=connector,
        )
        result = await service.run_session(stop)
        self.assertEqual(result["status"], "DERIBIT_OPTIONS_GREEKS_STREAM_STOPPED")
        self.assertEqual(connector.urls, [DERIBIT_PRODUCTION_WS_URL])
        sent = json.loads(websocket.sent[0])
        self.assertEqual(sent["method"], "public/subscribe")
        self.assertEqual(set(sent["params"]["channels"]), set(request["params"]["channels"]))

    async def test_subscription_ack_mismatch_fails_closed(self):
        websocket = _WebSocket([json.dumps({"jsonrpc": "2.0", "id": 42, "result": ["wrong.channel"]})])
        service = DeribitOptionsGreeksStreamService(
            instruments=_instruments(),
            store=_Store(),
            policy=DeribitOptionsGreeksStreamPolicy(enabled=True, max_expiries=1),
            clock=lambda: _t(),
            websocket_connector=_Connector(websocket),
        )
        with self.assertRaises(ValueError):
            await service.run_session(asyncio.Event())

    def test_architecture_keeps_stream_research_only(self):
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertEqual(contract["documented_subscription_method"], "public/subscribe")
        self.assertTrue(contract["selection_uses_expiry_not_inferred_delta"])
        self.assertEqual(contract["delta_source"], "DERIBIT_TICKER_GREEKS")
        self.assertFalse(contract["delta_inferred_from_strike"])
        self.assertTrue(contract["archive_requires_valid_25d_pair"])
        self.assertTrue(contract["missing_25d_pair_is_not_estimated"])
        self.assertFalse(contract["network_request_at_import"])
        self.assertFalse(contract["coindcx_contract_selection_allowed"])
        self.assertFalse(contract["coindcx_quote_fill_allowed"])
        self.assertFalse(contract["coindcx_pnl_replay_allowed"])
        self.assertFalse(contract["options_trade_generation_allowed"])
        self.assertFalse(contract["futures_trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
