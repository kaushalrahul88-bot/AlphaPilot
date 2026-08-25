import unittest

from app.providers.groww import GrowwProvider


class GrowwSymbolMappingTests(unittest.TestCase):
    def setUp(self):
        self.provider = object.__new__(GrowwProvider)

    def test_market_brain_and_fno_universe_symbols_are_mapped(self):
        for symbol in ("AXISBANK", "TATASTEEL", "HINDALCO", "ONGC", "MARUTI", "NESTLEIND"):
            self.assertEqual(
                self.provider._instrument(symbol),
                ("NSE", "CASH", symbol, f"NSE-{symbol}"),
            )

    def test_indexes_keep_explicit_mapping(self):
        self.assertEqual(
            self.provider._instrument("NIFTY"),
            ("NSE", "CASH", "NIFTY", "NSE-NIFTY"),
        )

    def test_unknown_symbol_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "not mapped"):
            self.provider._instrument("NOT_A_REAL_SYMBOL")


if __name__ == "__main__":
    unittest.main()
