from __future__ import annotations

import unittest

from app.crude_oil_mini_contracts import (
    CRUDE_OIL_MINI,
    normalize_crude_oil_mini_row,
    resolve_crude_oil_mini_universe,
)


def row(underlying, instrument_type, expiry, trading_symbol, *, strike="", lot=10):
    return {
        "exchange": "MCX",
        "segment": "COMMODITY",
        "underlying_symbol": underlying,
        "instrument_type": instrument_type,
        "expiry_date": expiry,
        "trading_symbol": trading_symbol,
        "groww_symbol": f"MCX-{trading_symbol}",
        "strike_price": str(strike),
        "lot_size": str(lot),
        "buy_allowed": "1",
    }


class CrudeOilMiniContractTests(unittest.TestCase):
    def test_regular_crude_is_never_admitted_as_mini(self):
        regular = row("CRUDEOIL", "CE", "2026-09-17", "CRUDEOIL17SEP268200CE", strike=8200, lot=100)
        self.assertIsNone(normalize_crude_oil_mini_row(regular))

    def test_mini_future_and_options_are_admitted(self):
        future = row(CRUDE_OIL_MINI, "FUT", "2026-09-21", "CRUDEOILM21SEP26FUT")
        call = row(CRUDE_OIL_MINI, "CE", "2026-09-17", "CRUDEOILM17SEP268200CE", strike=8200)
        self.assertEqual(normalize_crude_oil_mini_row(future)["instrument_type"], "FUT")
        self.assertEqual(normalize_crude_oil_mini_row(call)["option_type"], "CE")

    def test_option_expiry_is_not_inferred_from_future_expiry(self):
        rows = [
            row(CRUDE_OIL_MINI, "FUT", "2026-09-21", "CRUDEOILM21SEP26FUT"),
            row(CRUDE_OIL_MINI, "FUT", "2026-10-20", "CRUDEOILM20OCT26FUT"),
            row(CRUDE_OIL_MINI, "CE", "2026-09-17", "CRUDEOILM17SEP268200CE", strike=8200),
            row(CRUDE_OIL_MINI, "PE", "2026-09-17", "CRUDEOILM17SEP268200PE", strike=8200),
            row(CRUDE_OIL_MINI, "CE", "2026-10-15", "CRUDEOILM15OCT268200CE", strike=8200),
            row(CRUDE_OIL_MINI, "PE", "2026-10-15", "CRUDEOILM15OCT268200PE", strike=8200),
        ]
        result = resolve_crude_oil_mini_universe(rows, "2026-09-01")
        self.assertEqual(result["future"]["trading_symbol"], "CRUDEOILM21SEP26FUT")
        self.assertEqual(result["future_expiry"], "2026-09-21")
        self.assertEqual(result["nearest_option_expiry"], "2026-09-17")
        self.assertEqual(result["option_expiries"], ["2026-09-17", "2026-10-15"])
        self.assertEqual(result["nearest_option_types"], ["CE", "PE"])
        self.assertTrue(result["future_and_option_expiry_are_independent"])

    def test_nearest_option_expiry_must_have_both_sides(self):
        rows = [
            row(CRUDE_OIL_MINI, "FUT", "2026-09-21", "CRUDEOILM21SEP26FUT"),
            row(CRUDE_OIL_MINI, "CE", "2026-09-17", "CRUDEOILM17SEP268200CE", strike=8200),
        ]
        with self.assertRaises(RuntimeError):
            resolve_crude_oil_mini_universe(rows, "2026-09-01")


if __name__ == "__main__":
    unittest.main()
