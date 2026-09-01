from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.providers.groww_rate_limited import RateLimitedGrowwProvider

IST = ZoneInfo("Asia/Kolkata")


def future(symbol="CRUDEOILM21SEP26FUT", expiry="2026-09-21"):
    return {
        "underlying":"CRUDEOILM",
        "exchange":"MCX",
        "segment":"COMMODITY",
        "instrument_type":"FUT",
        "trading_symbol":symbol,
        "groww_symbol":f"MCX-{symbol}",
        "expiry":expiry,
        "lot_size":10,
        "strike":None,
        "option_type":None,
        "buy_allowed":True,
    }


def option(symbol="CRUDEOILM17SEP268200CE", option_type="CE", strike=8200):
    return {
        "underlying":"CRUDEOILM",
        "exchange":"MCX",
        "segment":"COMMODITY",
        "instrument_type":option_type,
        "trading_symbol":symbol,
        "groww_symbol":f"MCX-{symbol}",
        "expiry":"2026-09-17",
        "lot_size":10,
        "strike":float(strike),
        "option_type":option_type,
        "buy_allowed":True,
    }


class GrowwCrudeOilMiniProviderTests(unittest.TestCase):
    def test_only_mini_family_uses_mini_route(self):
        self.assertTrue(RateLimitedGrowwProvider._mini_symbol("CRUDEOILM"))
        self.assertTrue(RateLimitedGrowwProvider._mini_symbol("CRUDEOILM17SEP268200CE"))
        self.assertFalse(RateLimitedGrowwProvider._mini_symbol("CRUDEOIL"))
        self.assertFalse(RateLimitedGrowwProvider._mini_symbol("COPPER"))

    def test_underlying_resolves_current_mini_future(self):
        rows = [
            future(),
            option(),
            option("CRUDEOILM17SEP268200PE", "PE", 8200),
        ]
        selected = RateLimitedGrowwProvider._mini_contract_from_rows(
            rows, "CRUDEOILM", datetime(2026, 9, 1, 12, 0, tzinfo=IST)
        )
        self.assertEqual(selected["trading_symbol"], "CRUDEOILM21SEP26FUT")
        self.assertEqual(selected["instrument_type"], "FUT")
        self.assertEqual(selected["lot_size"], 10)

    def test_exact_option_resolves_exact_contract_not_future(self):
        wanted = option()
        selected = RateLimitedGrowwProvider._mini_contract_from_rows(
            [future(), wanted, option("CRUDEOILM17SEP268200PE", "PE", 8200)],
            wanted["trading_symbol"],
            datetime(2026, 9, 1, 12, 0, tzinfo=IST),
        )
        self.assertEqual(selected["trading_symbol"], wanted["trading_symbol"])
        self.assertEqual(selected["instrument_type"], "CE")
        self.assertEqual(selected["expiry"], "2026-09-17")

    def test_regular_crude_cannot_resolve_through_mini_exact_contract(self):
        with self.assertRaises(ValueError):
            RateLimitedGrowwProvider._mini_contract_from_rows(
                [future(), option()],
                "CRUDEOIL21SEP26FUT",
                datetime(2026, 9, 1, 12, 0, tzinfo=IST),
            )

    def test_timestamp_keys_normalize_to_ist_for_dedupe(self):
        iso = RateLimitedGrowwProvider._raw_timestamp_key("2026-08-31T04:00:00+00:00")
        self.assertEqual(iso, "2026-08-31T09:30:00+05:30")

    def test_legacy_epoch_candle_is_emitted_with_canonical_ist_timestamp(self):
        normalized = RateLimitedGrowwProvider._normalize_mini_candle(
            [1780285500, 8200, 8210, 8190, 8205, 100]
        )
        self.assertIsNotNone(normalized)
        timestamp, row = normalized
        self.assertEqual(timestamp, "2026-06-01T09:15:00+05:30")
        self.assertEqual(row[0], timestamp)
        self.assertEqual(row[1:], [8200, 8210, 8190, 8205, 100])

    def test_iso_candle_is_emitted_with_same_canonical_ist_timestamp(self):
        normalized = RateLimitedGrowwProvider._normalize_mini_candle(
            ["2026-09-01T04:30:00+00:00", 8230, 8240, 8220, 8233, 150]
        )
        self.assertIsNotNone(normalized)
        timestamp, row = normalized
        self.assertEqual(timestamp, "2026-09-01T10:00:00+05:30")
        self.assertEqual(row[0], timestamp)


if __name__ == "__main__":
    unittest.main()
