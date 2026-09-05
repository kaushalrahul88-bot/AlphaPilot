import unittest
from datetime import datetime, timedelta, timezone

from app.deribit_btc_options_ticker_greeks import (
    DeribitBtcOptionsGreeksBook,
    DeribitDeltaSkewPolicy,
    DeribitOptionInstrumentMeta,
    architecture_contract,
    normalize_option_instruments,
    ticker_capture_from_notification,
    ticker_subscription_channels,
)


def _t():
    return datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _meta(name, option_type, strike=100_000, days=2):
    return DeribitOptionInstrumentMeta(
        instrument_name=name,
        option_type=option_type,
        strike=strike,
        expiry_at=_t() + timedelta(days=days),
    ).validated()


def _notification(meta, *, delta, mark_iv, provider_at=None, gamma=0.00001, theta=-0.001, vega=0.02, rho=0.01):
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
                "mark_iv": mark_iv,
                "bid_iv": mark_iv - 1,
                "ask_iv": mark_iv + 1,
                "open_interest": 5,
                "greeks": {
                    "delta": delta,
                    "gamma": gamma,
                    "theta": theta,
                    "vega": vega,
                    "rho": rho,
                },
            },
        },
    }


def _capture(meta, *, delta, mark_iv, first_seen=None, provider_at=None):
    first_seen = first_seen or (_t() + timedelta(seconds=1))
    provider_at = provider_at or _t()
    return ticker_capture_from_notification(
        _notification(meta, delta=delta, mark_iv=mark_iv, provider_at=provider_at),
        instruments={meta.instrument_name: meta},
        first_seen_at=first_seen,
    )


class DeribitBtcOptionsTickerGreeksTests(unittest.TestCase):
    def test_normalizes_authoritative_option_metadata_without_name_parsing(self):
        expiry = _t() + timedelta(days=2)
        rows = [
            {
                "instrument_name": "opaque-call",
                "kind": "option",
                "is_active": True,
                "state": "open",
                "expiration_timestamp": int(expiry.timestamp() * 1000),
                "strike": 101_000,
                "option_type": "call",
            },
            {
                "instrument_name": "opaque-put",
                "kind": "option",
                "is_active": True,
                "state": "open",
                "expiration_timestamp": int(expiry.timestamp() * 1000),
                "strike": 99_000,
                "option_type": "put",
            },
        ]
        normalized = normalize_option_instruments(rows, as_of=_t())
        self.assertEqual(normalized["opaque-call"].option_type, "call")
        self.assertEqual(normalized["opaque-put"].strike, 99_000)

    def test_subscription_channels_are_public_agg2_by_default(self):
        channels = ticker_subscription_channels([_meta("BTC-C", "call"), _meta("BTC-P", "put")])
        self.assertEqual(channels, ("ticker.BTC-C.agg2", "ticker.BTC-P.agg2"))
        with self.assertRaises(ValueError):
            ticker_subscription_channels([_meta("BTC-C", "call")], interval="raw")

    def test_ticker_notification_preserves_observed_black_scholes_greeks(self):
        meta = _meta("BTC-C", "call")
        capture = _capture(meta, delta=0.26, mark_iv=54)
        self.assertEqual(capture.delta, 0.26)
        self.assertEqual(capture.mark_iv_pct, 54)
        self.assertGreater(capture.gamma, 0)
        self.assertLess(capture.theta, 0)
        self.assertGreater(capture.vega, 0)
        self.assertEqual(capture.option_type, "call")

    def test_channel_payload_instrument_mismatch_fails_closed(self):
        meta = _meta("BTC-C", "call")
        msg = _notification(meta, delta=0.25, mark_iv=50)
        msg["params"]["channel"] = "ticker.OTHER.agg2"
        with self.assertRaises(ValueError):
            ticker_capture_from_notification(msg, instruments={meta.instrument_name: meta}, first_seen_at=_t() + timedelta(seconds=1))

    def test_provider_timestamp_after_first_seen_is_rejected(self):
        meta = _meta("BTC-C", "call")
        with self.assertRaises(ValueError):
            _capture(
                meta,
                delta=0.25,
                mark_iv=50,
                first_seen=_t(),
                provider_at=_t() + timedelta(seconds=1),
            )

    def test_call_and_put_delta_signs_are_enforced(self):
        with self.assertRaises(ValueError):
            _capture(_meta("BTC-C", "call"), delta=-0.25, mark_iv=50)
        with self.assertRaises(ValueError):
            _capture(_meta("BTC-P", "put"), delta=0.25, mark_iv=50)

    def test_25d_skew_uses_nearest_observed_delta_pair_and_put_minus_call_iv(self):
        book = DeribitBtcOptionsGreeksBook()
        call_far = _capture(_meta("BTC-C-FAR", "call", 103_000), delta=0.40, mark_iv=50)
        call_25 = _capture(_meta("BTC-C-25", "call", 108_000), delta=0.24, mark_iv=52)
        put_far = _capture(_meta("BTC-P-FAR", "put", 97_000), delta=-0.40, mark_iv=54)
        put_25 = _capture(_meta("BTC-P-25", "put", 92_000), delta=-0.27, mark_iv=58)
        for row in (call_far, call_25, put_far, put_25):
            book.ingest(row)
        snapshot = book.snapshot_25d(as_of=_t() + timedelta(seconds=2))
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.call.instrument_name, "BTC-C-25")
        self.assertEqual(snapshot.put.instrument_name, "BTC-P-25")
        self.assertAlmostEqual(snapshot.put_call_skew_25d_iv_points, 6.0)
        self.assertAlmostEqual(snapshot.call_delta_distance, 0.01)
        self.assertAlmostEqual(snapshot.put_delta_distance, 0.02)

    def test_poor_delta_match_returns_none_instead_of_approximation(self):
        book = DeribitBtcOptionsGreeksBook(DeribitDeltaSkewPolicy(max_delta_distance=0.03))
        book.ingest(_capture(_meta("BTC-C", "call"), delta=0.50, mark_iv=50))
        book.ingest(_capture(_meta("BTC-P", "put"), delta=-0.50, mark_iv=55))
        self.assertIsNone(book.snapshot_25d(as_of=_t() + timedelta(seconds=2)))

    def test_stale_or_future_tickers_do_not_form_skew(self):
        policy = DeribitDeltaSkewPolicy(max_ticker_age_seconds=5)
        stale = DeribitBtcOptionsGreeksBook(policy)
        stale.ingest(_capture(_meta("BTC-C", "call"), delta=0.25, mark_iv=50, first_seen=_t() + timedelta(seconds=1)))
        stale.ingest(_capture(_meta("BTC-P", "put"), delta=-0.25, mark_iv=55, first_seen=_t() + timedelta(seconds=1)))
        self.assertIsNone(stale.snapshot_25d(as_of=_t() + timedelta(seconds=10)))

        future = DeribitBtcOptionsGreeksBook(policy)
        future.ingest(_capture(_meta("BTC-C2", "call"), delta=0.25, mark_iv=50, first_seen=_t() + timedelta(seconds=4), provider_at=_t() + timedelta(seconds=3)))
        future.ingest(_capture(_meta("BTC-P2", "put"), delta=-0.25, mark_iv=55, first_seen=_t() + timedelta(seconds=4), provider_at=_t() + timedelta(seconds=3)))
        self.assertIsNone(future.snapshot_25d(as_of=_t() + timedelta(seconds=2)))

    def test_pair_first_seen_gap_must_be_temporally_aligned(self):
        policy = DeribitDeltaSkewPolicy(max_pair_first_seen_gap_seconds=2, max_ticker_age_seconds=30)
        book = DeribitBtcOptionsGreeksBook(policy)
        book.ingest(_capture(_meta("BTC-C", "call"), delta=0.25, mark_iv=50, first_seen=_t() + timedelta(seconds=1)))
        book.ingest(_capture(_meta("BTC-P", "put"), delta=-0.25, mark_iv=55, first_seen=_t() + timedelta(seconds=5), provider_at=_t() + timedelta(seconds=4)))
        self.assertIsNone(book.snapshot_25d(as_of=_t() + timedelta(seconds=6)))

    def test_provider_updates_are_monotonic_per_instrument(self):
        meta = _meta("BTC-C", "call")
        book = DeribitBtcOptionsGreeksBook()
        current = _capture(meta, delta=0.25, mark_iv=50, provider_at=_t(), first_seen=_t() + timedelta(seconds=1))
        self.assertEqual(book.ingest(current)["status"], "TICKER_STATE_UPDATED")
        self.assertEqual(book.ingest(current)["status"], "IDEMPOTENT_DUPLICATE")
        older = _capture(meta, delta=0.26, mark_iv=51, provider_at=_t() - timedelta(seconds=1), first_seen=_t() + timedelta(seconds=2))
        self.assertEqual(book.ingest(older)["status"], "STALE_PROVIDER_UPDATE_IGNORED")
        conflicting = _capture(meta, delta=0.26, mark_iv=51, provider_at=_t(), first_seen=_t() + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            book.ingest(conflicting)

    def test_architecture_keeps_greeks_context_out_of_execution(self):
        contract = architecture_contract()
        self.assertTrue(contract["delta_is_observed_black_scholes_delta"])
        self.assertFalse(contract["delta_inferred_from_strike"])
        self.assertEqual(contract["skew_definition"], "PUT_25D_MARK_IV_MINUS_CALL_25D_MARK_IV")
        self.assertFalse(contract["coindcx_contract_selection_allowed"])
        self.assertFalse(contract["coindcx_quote_fill_allowed"])
        self.assertFalse(contract["coindcx_pnl_replay_allowed"])
        self.assertFalse(contract["underlying_direction_generation_allowed"])
        self.assertFalse(contract["options_trade_generation_allowed"])
        self.assertFalse(contract["futures_trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
