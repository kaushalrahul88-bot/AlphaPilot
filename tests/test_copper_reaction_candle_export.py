import unittest
from app.copper_reaction_candle_export import export_copper_reaction_candles_from_store
from app.copper_market_brain_direction_audit import PRIMARY_END, PRIMARY_START, REFERENCE_CONTRACT


class FakeStore:
    def __init__(self, segments): self.segments=segments; self.calls=[]
    async def initialize(self): self.calls.append(("initialize",))
    async def read_symbol_contract_segments(self,*args): self.calls.append(("read",)+args); return self.segments


class CopperReactionCandleExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_exact_frozen_reference_contract_range(self):
        candles=[["2026-08-07T10:00:00+05:30",1,2,1,2,3,0]]
        store=FakeStore([{"trading_symbol":REFERENCE_CONTRACT,"candles":candles}])
        r=await export_copper_reaction_candles_from_store(store,exported_at="2026-08-31T12:00:00+00:00")
        self.assertEqual(store.calls[1],("read","COPPER",5,PRIMARY_START,PRIMARY_END))
        self.assertEqual(r["trading_symbol"],REFERENCE_CONTRACT)
        self.assertEqual(r["candle_count"],1)
        self.assertFalse(r["network_refetch"])

    async def test_missing_reference_contract_fails_closed(self):
        with self.assertRaises(RuntimeError):
            await export_copper_reaction_candles_from_store(FakeStore([]))

    async def test_empty_reference_contract_fails_closed(self):
        store=FakeStore([{"trading_symbol":REFERENCE_CONTRACT,"candles":[]}])
        with self.assertRaises(RuntimeError):
            await export_copper_reaction_candles_from_store(store)


if __name__=="__main__": unittest.main()
