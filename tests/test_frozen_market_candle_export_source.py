import unittest
from app.frozen_market_candle_export import build_frozen_candle_artifact


class FrozenExportSourceTests(unittest.TestCase):
    def test_source_is_retained(self):
        source="persistent_store.read_symbol_contract_segments"
        r=build_frozen_candle_artifact([],symbol="COPPER",trading_symbol="COPPER31AUG26FUT",interval_minutes=5,
                                       start="2026-08-03T09:00:00+05:30",end="2026-08-28T23:30:00+05:30",
                                       source=source,exported_at="2026-08-31T12:00:00+00:00")
        self.assertEqual(r["source"],source)


if __name__=="__main__":unittest.main()
