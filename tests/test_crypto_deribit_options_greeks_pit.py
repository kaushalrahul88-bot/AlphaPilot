import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_deribit_options_greeks_pit import DATASET, architecture_contract, deribit_greeks_archive_record
from app.deribit_btc_options_ticker_greeks import (
    DeribitBtcDeltaSkewSnapshot,
    DeribitOptionInstrumentMeta,
    ticker_capture_from_notification,
)


def _t(seconds=0):
    return datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def _capture(name, option_type, delta, iv, strike):
    meta = DeribitOptionInstrumentMeta(
        instrument_name=name,
        option_type=option_type,
        strike=strike,
        expiry_at=_t() + timedelta(days=2),
    )
    msg = {
        "method": "subscription",
        "params": {
            "channel": f"ticker.{name}.agg2",
            "data": {
                "instrument_name": name,
                "timestamp": int(_t().timestamp() * 1000),
                "underlying_price": 100_000,
                "mark_iv": iv,
                "bid_iv": iv - 1,
                "ask_iv": iv + 1,
                "open_interest": 4,
                "greeks": {"delta": delta, "gamma": 0.00001, "theta": -0.001, "vega": 0.02, "rho": 0.01},
            },
        },
    }
    return ticker_capture_from_notification(msg, instruments={name: meta}, first_seen_at=_t(1))


def _snapshot():
    call = _capture("BTC-C25", "call", 0.24, 52, 108_000)
    put = _capture("BTC-P25", "put", -0.26, 58, 92_000)
    return DeribitBtcDeltaSkewSnapshot(
        first_seen_at=_t(1),
        provider_time=_t(),
        expiry_at=call.expiry_at,
        target_abs_delta=0.25,
        call=call,
        put=put,
        call_delta_distance=0.01,
        put_delta_distance=0.01,
        put_call_skew_25d_iv_points=6.0,
    ).validated()


class CryptoDeribitOptionsGreeksPitTests(unittest.TestCase):
    def test_archive_preserves_observed_ticker_greeks_and_execution_barriers(self):
        record = deribit_greeks_archive_record(_snapshot())
        frozen = record.frozen_dict()
        self.assertEqual(frozen["dataset"], DATASET)
        self.assertEqual(frozen["provider"], "DERIBIT_TICKER")
        self.assertEqual(frozen["event_at"], _t().isoformat())
        payload = frozen["payload"]
        self.assertEqual(payload["put_call_skew_25d_iv_points"], 6.0)
        self.assertTrue(payload["skew_25d_observed_from_ticker_delta"])
        self.assertFalse(payload["skew_25d_inferred_from_strike"])
        self.assertEqual(payload["call"]["delta"], 0.24)
        self.assertEqual(payload["put"]["delta"], -0.26)
        self.assertFalse(payload["coindcx_contract_selection_allowed"])
        self.assertFalse(payload["coindcx_quote_fill_allowed"])
        self.assertFalse(payload["coindcx_pnl_replay_allowed"])
        self.assertFalse(payload["trade_generation_allowed"])

    def test_greeks_record_is_invisible_before_pair_first_seen(self):
        ledger = ImmutableBtcPitLedger()
        record = deribit_greeks_archive_record(_snapshot())
        ledger.insert_first_seen(record)
        self.assertEqual(ledger.visible_as_of(_t(0)), [])
        self.assertEqual(len(ledger.visible_as_of(_t(1), dataset=DATASET)), 1)

    def test_contract_keeps_delta_observed_and_context_only(self):
        contract = architecture_contract()
        self.assertTrue(contract["ticker_delta_observed"])
        self.assertFalse(contract["delta_inferred_from_strike"])
        self.assertFalse(contract["coindcx_contract_selection_allowed"])
        self.assertFalse(contract["coindcx_quote_fill_allowed"])
        self.assertFalse(contract["coindcx_pnl_replay_allowed"])
        self.assertFalse(contract["underlying_direction_assigned"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
