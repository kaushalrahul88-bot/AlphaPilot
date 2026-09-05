import unittest
from datetime import datetime, timedelta, timezone

from app.deribit_btc_options_context_provider import (
    BOOK_SUMMARY_URL,
    INSTRUMENTS_URL,
    DeribitBtcOptionsContextPolicy,
    DeribitBtcOptionsContextProvider,
    architecture_contract,
)


def _t():
    return datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _ms(dt):
    return int(dt.timestamp() * 1000)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, instruments, summaries):
        self.instruments = instruments
        self.summaries = summaries
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        if url == INSTRUMENTS_URL:
            return _Response({"jsonrpc": "2.0", "result": self.instruments})
        if url == BOOK_SUMMARY_URL:
            return _Response({"jsonrpc": "2.0", "result": self.summaries})
        raise AssertionError(url)


def _instrument(name, expiry, strike, option_type, **extra):
    row = {
        "instrument_name": name,
        "kind": "option",
        "is_active": True,
        "state": "open",
        "expiration_timestamp": _ms(expiry),
        "strike": strike,
        "option_type": option_type,
    }
    row.update(extra)
    return row


def _summary(name, iv, oi, underlying=100_000.0):
    return {
        "instrument_name": name,
        "mark_iv": iv,
        "open_interest": oi,
        "underlying_price": underlying,
    }


def _chain():
    e1 = _t() + timedelta(days=2)
    e2 = _t() + timedelta(days=9)
    instruments = [
        _instrument("BTC-E1-95000-C", e1, 95_000, "call"),
        _instrument("BTC-E1-95000-P", e1, 95_000, "put"),
        _instrument("BTC-E1-100000-C", e1, 100_000, "call"),
        _instrument("BTC-E1-100000-P", e1, 100_000, "put"),
        _instrument("BTC-E1-105000-C", e1, 105_000, "call"),
        _instrument("BTC-E1-105000-P", e1, 105_000, "put"),
        _instrument("BTC-E2-100000-C", e2, 100_000, "call"),
        _instrument("BTC-E2-100000-P", e2, 100_000, "put"),
        _instrument("BTC-E2-110000-C", e2, 110_000, "call"),
        _instrument("BTC-E2-110000-P", e2, 110_000, "put"),
    ]
    summaries = [
        _summary("BTC-E1-95000-C", 60, 2),
        _summary("BTC-E1-95000-P", 62, 3),
        _summary("BTC-E1-100000-C", 55, 5),
        _summary("BTC-E1-100000-P", 57, 7),
        _summary("BTC-E1-105000-C", 61, 1),
        _summary("BTC-E1-105000-P", 63, 2),
        _summary("BTC-E2-100000-C", 58, 4),
        _summary("BTC-E2-100000-P", 60, 6),
        _summary("BTC-E2-110000-C", 65, 3),
        _summary("BTC-E2-110000-P", 67, 4),
    ]
    return instruments, summaries, e1, e2


class DeribitBtcOptionsContextProviderTests(unittest.TestCase):
    def test_disabled_provider_fails_before_network(self):
        instruments, summaries, _, _ = _chain()
        client = _Client(instruments, summaries)
        provider = DeribitBtcOptionsContextProvider(client=client, clock=_t)
        with self.assertRaises(RuntimeError):
            provider.capture_context()
        self.assertEqual(client.calls, [])

    def test_documented_option_endpoints_and_params_are_used_on_initial_seed(self):
        instruments, summaries, _, _ = _chain()
        client = _Client(instruments, summaries)
        provider = DeribitBtcOptionsContextProvider(
            DeribitBtcOptionsContextPolicy(enabled=True), client=client, clock=_t
        )
        provider.capture_context()
        self.assertEqual(client.calls[0][0], INSTRUMENTS_URL)
        self.assertEqual(client.calls[0][1], {"currency": "BTC", "kind": "option", "expired": "false"})
        self.assertEqual(client.calls[1][0], BOOK_SUMMARY_URL)
        self.assertEqual(client.calls[1][1], {"currency": "BTC", "kind": "option"})

    def test_subsequent_snapshot_reuses_instrument_cache_and_only_refreshes_summary(self):
        instruments, summaries, _, _ = _chain()
        client = _Client(instruments, summaries)
        provider = DeribitBtcOptionsContextProvider(
            DeribitBtcOptionsContextPolicy(enabled=True), client=client, clock=_t
        )
        provider.capture_context()
        provider.capture_context()
        urls = [call[0] for call in client.calls]
        self.assertEqual(urls.count(INSTRUMENTS_URL), 1)
        self.assertEqual(urls.count(BOOK_SUMMARY_URL), 2)

    def test_explicit_instrument_refresh_is_available_but_not_automatic(self):
        instruments, summaries, _, _ = _chain()
        client = _Client(instruments, summaries)
        provider = DeribitBtcOptionsContextProvider(
            DeribitBtcOptionsContextPolicy(enabled=True), client=client, clock=_t
        )
        provider.capture_context()
        count = provider.refresh_instruments()
        self.assertEqual(count, len(instruments))
        self.assertEqual([call[0] for call in client.calls].count(INSTRUMENTS_URL), 2)

    def test_capture_pairs_atm_call_put_and_computes_term_structure_and_oi(self):
        instruments, summaries, e1, e2 = _chain()
        provider = DeribitBtcOptionsContextProvider(
            DeribitBtcOptionsContextPolicy(enabled=True),
            client=_Client(instruments, summaries),
            clock=_t,
        )
        capture = provider.capture_context()
        self.assertEqual(capture.first_seen_at, _t())
        self.assertEqual(capture.nearest_expiry_at, e1)
        self.assertEqual(capture.next_expiry_at, e2)
        self.assertEqual(capture.atm_mark_iv_pct, 56.0)
        self.assertEqual(capture.next_expiry_atm_mark_iv_pct, 59.0)
        self.assertEqual(capture.term_structure_slope_iv_points, 3.0)
        self.assertEqual(capture.total_call_open_interest_btc, 15.0)
        self.assertEqual(capture.total_put_open_interest_btc, 22.0)
        self.assertAlmostEqual(capture.put_call_open_interest_ratio, 22.0 / 15.0)
        self.assertEqual(capture.matched_contract_count, 10)
        self.assertEqual(capture.active_contract_count, 10)
        self.assertEqual(capture.valid_expiry_count, 2)
        self.assertIsNone(capture.skew_25d)

    def test_unpaired_nearest_strike_uses_nearest_common_call_put_strike(self):
        expiry = _t() + timedelta(days=3)
        instruments = [
            _instrument("BTC-99000-C", expiry, 99_000, "call"),
            _instrument("BTC-100000-C", expiry, 100_000, "call"),
            _instrument("BTC-100000-P", expiry, 100_000, "put"),
        ]
        summaries = [
            _summary("BTC-99000-C", 40, 1),
            _summary("BTC-100000-C", 50, 1),
            _summary("BTC-100000-P", 54, 1),
        ]
        provider = DeribitBtcOptionsContextProvider(
            DeribitBtcOptionsContextPolicy(enabled=True),
            client=_Client(instruments, summaries),
            clock=_t,
        )
        capture = provider.capture_context()
        self.assertEqual(capture.atm_mark_iv_pct, 52.0)

    def test_expiring_too_soon_contracts_are_excluded(self):
        near = _t() + timedelta(minutes=30)
        later = _t() + timedelta(days=2)
        instruments = [
            _instrument("BTC-NEAR-C", near, 100_000, "call"),
            _instrument("BTC-NEAR-P", near, 100_000, "put"),
            _instrument("BTC-LATER-C", later, 100_000, "call"),
            _instrument("BTC-LATER-P", later, 100_000, "put"),
        ]
        summaries = [
            _summary("BTC-NEAR-C", 90, 10),
            _summary("BTC-NEAR-P", 90, 10),
            _summary("BTC-LATER-C", 50, 2),
            _summary("BTC-LATER-P", 52, 2),
        ]
        provider = DeribitBtcOptionsContextProvider(
            DeribitBtcOptionsContextPolicy(enabled=True, min_seconds_to_expiry=3600),
            client=_Client(instruments, summaries),
            clock=_t,
        )
        capture = provider.capture_context()
        self.assertEqual(capture.nearest_expiry_at, later)
        self.assertEqual(capture.atm_mark_iv_pct, 51.0)
        self.assertEqual(capture.active_contract_count, 2)

    def test_no_paired_call_put_expiry_fails_closed(self):
        expiry = _t() + timedelta(days=2)
        instruments = [_instrument("BTC-ONLY-C", expiry, 100_000, "call")]
        summaries = [_summary("BTC-ONLY-C", 50, 1)]
        provider = DeribitBtcOptionsContextProvider(
            DeribitBtcOptionsContextPolicy(enabled=True),
            client=_Client(instruments, summaries),
            clock=_t,
        )
        with self.assertRaises(ValueError):
            provider.capture_context()

    def test_policy_and_architecture_keep_deribit_context_only(self):
        with self.assertRaises(ValueError):
            DeribitBtcOptionsContextPolicy(currency="ETH").validated()
        with self.assertRaises(ValueError):
            DeribitBtcOptionsContextPolicy(max_expiries_for_term_structure=1).validated()
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertTrue(contract["instrument_list_seeded_lazily"])
        self.assertFalse(contract["instrument_list_polled_each_snapshot"])
        self.assertTrue(contract["instrument_refresh_explicit"])
        self.assertTrue(contract["mark_iv_captured"])
        self.assertTrue(contract["open_interest_captured"])
        self.assertFalse(contract["skew_25d_inferred_from_strike"])
        self.assertFalse(contract["coindcx_contract_selection_allowed"])
        self.assertFalse(contract["coindcx_quote_fill_allowed"])
        self.assertFalse(contract["coindcx_pnl_replay_allowed"])
        self.assertFalse(contract["underlying_direction_generation_allowed"])
        self.assertFalse(contract["options_trade_generation_allowed"])
        self.assertFalse(contract["futures_trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
