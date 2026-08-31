import unittest
from scripts.export_copper_reaction_candles import export_from_store


class FakeStore:
    def __init__(self):self.calls=[]
    async def initialize(self):pass
    async def read_symbol_contract_segments(self,symbol,interval,start,end):
        self.calls.append((symbol,interval,start,end))
        return [{"trading_symbol":"OTHER","candles":[]},
                {"trading_symbol":"COPPER31AUG26FUT","expiry_date":"2026-08-31",
                 "candles":[["2026-08-07T10:00:00+05:30",100,101,99,100.5,10]]}]


class CopperReactionExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_exact_stored_reference_contract_without_refetch(self):
        store=FakeStore();artifact=await export_from_store(store)
        self.assertEqual(store.calls[0][0:2],("COPPER",5))
        self.assertEqual(artifact["trading_symbol"],"COPPER31AUG26FUT")
        self.assertEqual(artifact["source"],"persistent_store.read_symbol_contract_segments")
        self.assertFalse(artifact["network_refetch"])
        self.assertEqual(artifact["candle_count"],1)


if __name__=="__main__":unittest.main()
